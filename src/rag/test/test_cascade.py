from __future__ import annotations
 
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Tuple
 
from pydantic import BaseModel, Field
 
 
# ═══════════════════════════════════════════════════════════
# CascadeRouter + schema  (ตอนแยกไฟล์จริง import จาก cascade.py แทน)
# ═══════════════════════════════════════════════════════════
class JobStatus(str, Enum):
    DRAFT = "draft"
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
 
 
class RouteID(str, Enum):
    WRITE_JOB_RECORD = "write_job_record"
    RAG_ANSWER = "rag_answer"
    OUT_OF_SCOPE_REFUSE = "out_of_scope_refuse"
    ERROR_CODE_LOOKUP = "error_code_lookup"
 
 
class HVACJobRecord(BaseModel):
    raw_query: str
    status: JobStatus = JobStatus.DRAFT
    error_code: Optional[str] = None
    component: Optional[str] = None
    action: Optional[str] = None
 
 
class RouteDecision(BaseModel):
    route: RouteID
    layer: int
    distance: Optional[float] = None
    matched_error_code: Optional[str] = None
    intent: Optional[str] = None
    enrichment_used: bool = False
 
 
class SessionLog(BaseModel):
    query: str
    decision: RouteDecision
    job_record: Optional[HVACJobRecord] = None
    content: Optional[str] = None
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
 
 
class CascadeRouter:
    def __init__(self, *, regex_error_code, regex_job_keyword, error_code_lookup,
                 llm_classify, retrieve, rag_answer, write_job_record, tau: float):
        self.regex_error_code = regex_error_code
        self.regex_job_keyword = regex_job_keyword
        self.error_code_lookup = error_code_lookup
        self.llm_classify = llm_classify
        self.retrieve = retrieve
        self.rag_answer = rag_answer
        self.write_job_record = write_job_record
        self.tau = tau
 
    def handle_query(self, query: str) -> SessionLog:
        # ชั้น 1: regex
        code = self.regex_error_code(query)
        table_row = self.error_code_lookup(code) if code else None
 
        if self.regex_job_keyword(query):
            return self._log(query, RouteID.WRITE_JOB_RECORD, layer=1,
                             matched_error_code=code,
                             job_record=self.write_job_record(query))
 
        # ชั้น 2: LLM classify
        intent = self.llm_classify(query)
        if intent == "job":
            return self._log(query, RouteID.WRITE_JOB_RECORD, layer=2,
                             intent=intent, matched_error_code=code,
                             job_record=self.write_job_record(query))
 
        # ชั้น 3: retrieve + distance gate (guard: table_row is None)
        context, distance = self.retrieve(query)
        if distance > self.tau and table_row is None:
            return self._log(query, RouteID.OUT_OF_SCOPE_REFUSE, layer=3,
                             distance=distance, intent=intent)
 
        answer = self.rag_answer(query, context, table_row)
        return self._log(query, RouteID.RAG_ANSWER, layer=3,
                         distance=distance, intent=intent, matched_error_code=code,
                         enrichment_used=table_row is not None, content=answer)
 
    @staticmethod
    def _log(query, route, *, layer, distance=None, matched_error_code=None,
             intent=None, enrichment_used=False, job_record=None, content=None):
        return SessionLog(
            query=query,
            decision=RouteDecision(
                route=route, layer=layer, distance=distance,
                matched_error_code=matched_error_code, intent=intent,
                enrichment_used=enrichment_used,
            ),
            job_record=job_record, content=content,
        )
 
 
# ═══════════════════════════════════════════════════════════
# Fakes (dependency injection) — regex จริง + fake ที่คุมค่าได้
# ═══════════════════════════════════════════════════════════
ERROR_RE = re.compile(r"([EPFU]\d{1,2})", re.IGNORECASE)   # [FIX] เอา \b ออก → จับไทยติดกันได้
JOB_RE = re.compile(r"(บันทึกงาน|จดงาน|log ?งาน|บันทึกการซ่อม)")
 
 
def demo_error_code(q):
    m = ERROR_RE.search(q)
    return m.group(1).upper() if m else None
 
 
def demo_job_kw(q):
    return bool(JOB_RE.search(q))
 
 
def demo_lookup(code):
    return f"{code} = (row จากตาราง 3.1)" if code else None
 
 
def demo_rag(q, ctx, row):
    return f"[ตอบจาก ctx{' + ' + row if row else ''}]"
 
 
def demo_write(q):
    return HVACJobRecord(raw_query=q)
 
 
def make_router(*, distance=0.35, intent="knowledge", lookup=demo_lookup):
    """helper สร้าง router — ปรับ distance/intent/lookup ต่อเคสได้"""
    return CascadeRouter(
        regex_error_code=demo_error_code,
        regex_job_keyword=demo_job_kw,
        error_code_lookup=lookup,
        llm_classify=lambda q: intent,
        retrieve=lambda q: ("ctx", distance),
        rag_answer=demo_rag,
        write_job_record=demo_write,
        tau=0.5,
    )
 
 
# ═══════════════════════════════════════════════════════════
# 6 unit tests
# ═══════════════════════════════════════════════════════════
def test_1_thai_glued_error_code():
    """ไทยติด code 'ขึ้นE5' → rag + enrichment (พิสูจน์บั๊ก \\b หายจริง)"""
    router = make_router(distance=0.35)
    log = router.handle_query("แอร์ขึ้นE5")
    assert log.decision.route == RouteID.RAG_ANSWER
    assert log.decision.enrichment_used is True
    print("✅ เคส 1: ไทยติด code → rag + enrichment")
 
 
def test_2_e99_fake_code():
    """E99 หน้าตาเหมือน code แต่ไม่มีในตาราง → refuse (guard table_row is None)"""
    def lookup_no_e99(code):
        if code == "E99":
            return None
        return demo_lookup(code)
 
    router = make_router(distance=0.75, lookup=lookup_no_e99)
    log = router.handle_query("E99 แก้ยังไง")
    assert log.decision.route == RouteID.OUT_OF_SCOPE_REFUSE
    print("✅ เคส 2: E99 ปลอม → refuse")
 
 
def test_3_job_keyword_layer1():
    """'บันทึกงาน...' → write_job_record จับที่ชั้น 1 (regex)"""
    router = make_router()
    log = router.handle_query("บันทึกงาน เปลี่ยนคาปาซิเตอร์ที่บ้านลูกค้า")
    assert log.decision.route == RouteID.WRITE_JOB_RECORD
    assert log.decision.layer == 1
    print("✅ เคส 3: job keyword → write (layer 1)")
 
 
def test_4_job_intent_layer2():
    """job ที่ regex ไม่จับ → LLM classify='job' → write ชั้น 2"""
    router = make_router(intent="job")
    log = router.handle_query("เปลี่ยนคอมเสร็จแล้ว คิด 1200")
    assert log.decision.route == RouteID.WRITE_JOB_RECORD
    assert log.decision.layer == 2
    print("✅ เคส 4: job intent → write (layer 2)")
 
 
def test_5_out_of_scope():
    """คำถามนอกขอบเขต ไม่มี code + distance สูง → refuse"""
    router = make_router(distance=0.8)
    log = router.handle_query("วันนี้อากาศเป็นไงบ้าง")
    assert log.decision.route == RouteID.OUT_OF_SCOPE_REFUSE
    print("✅ เคส 5: out-of-scope → refuse")
 
 
def test_6_e5_in_scope_enrichment():
    """E5 มีจริงในตาราง + distance ต่ำ → rag + enrichment"""
    router = make_router(distance=0.35)
    log = router.handle_query("E5 แก้ยังไง")
    assert log.decision.route == RouteID.RAG_ANSWER
    assert log.decision.enrichment_used is True
    print("✅ เคส 6: E5 in-scope → rag + enrichment")
 
 
if __name__ == "__main__":
    tests = [
        test_1_thai_glued_error_code,
        test_2_e99_fake_code,
        test_3_job_keyword_layer1,
        test_4_job_intent_layer2,
        test_5_out_of_scope,
        test_6_e5_in_scope_enrichment,
    ]
    print("=" * 50)
    for t in tests:
        t()
    print("=" * 50)
    print(f"🎉 ผ่านครบ {len(tests)}/{len(tests)} เคส")