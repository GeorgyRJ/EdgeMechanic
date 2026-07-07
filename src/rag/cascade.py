from __future__ import annotations
 
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Tuple
 
from pydantic import BaseModel, Field
 
 
# ==========================================
# 1. Trace schema  (RouteDecision อยู่ใน SessionLog; HVACJobRecord optional)
# ==========================================
class JobStatus(str, Enum):
    DRAFT = "draft"
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
 
 
class RouteID(str, Enum):
    """stable id — co-designed กับ trace/eval ตั้งแต่ต้น (retrofit ทีหลังทำ eval พัง)."""
    WRITE_JOB_RECORD = "write_job_record"
    RAG_ANSWER = "rag_answer"
    OUT_OF_SCOPE_REFUSE = "out_of_scope_refuse"
    ERROR_CODE_LOOKUP = "error_code_lookup"   # merge/enrichment เท่านั้น ไม่เคยเป็น route ปลายทาง
 
 
class HVACJobRecord(BaseModel):
    """placeholder — Georgy เป็นเจ้าของ schema จริง (field ค่อยเติมตอน week 4+)."""
    raw_query: str
    status: JobStatus = JobStatus.DRAFT
    error_code: Optional[str] = None
    component: Optional[str] = None
    action: Optional[str] = None
 
 
class RouteDecision(BaseModel):
    """instrumentation สำหรับ eval — บันทึกว่า layer ไหน route ไปไหน ด้วยสัญญาณอะไร."""
    route: RouteID
    layer: int
    distance: Optional[float] = None
    matched_error_code: Optional[str] = None   # signal จาก regex ชั้น 1
    intent: Optional[str] = None               # ผลจาก llm_classify ชั้น 2
    enrichment_used: bool = False              # error_code_lookup ถูก merge เข้า answer ไหม
 
 
class SessionLog(BaseModel):
    """outer container. RouteDecision อยู่ข้างใน; HVACJobRecord มีเฉพาะ write path."""
    query: str
    decision: RouteDecision
    job_record: Optional[HVACJobRecord] = None
    content: Optional[str] = None              # คำตอบ (rag_answer path)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
 
 
# ==========================================
# 2. Dependency signatures (inject ของจริงหรือ fake ตอนเทส)
# ==========================================
RegexErrorCodeFn = Callable[[str], Optional[str]]      # query -> "E5" | None
RegexJobKeywordFn = Callable[[str], bool]              # query -> bool
ErrorCodeLookupFn = Callable[[str], Optional[str]]     # code  -> row text | None
LlmClassifyFn = Callable[[str], str]                   # query -> "job" | "knowledge"
RetrieveFn = Callable[[str], Tuple[str, float]]        # query -> (context, best_distance)
RagAnswerFn = Callable[[str, str, Optional[str]], str] # (query, context, row) -> answer
WriteJobRecordFn = Callable[[str], HVACJobRecord]      # query -> record
 
 
# ==========================================
# 3. Cascade
# ==========================================
class CascadeRouter:
    def __init__(
        self,
        *,
        regex_error_code: RegexErrorCodeFn,
        regex_job_keyword: RegexJobKeywordFn,
        error_code_lookup: ErrorCodeLookupFn,
        llm_classify: LlmClassifyFn,
        retrieve: RetrieveFn,
        rag_answer: RagAnswerFn,
        write_job_record: WriteJobRecordFn,
        tau: float,   # distance gate threshold — calibrate ก่อนใช้จริง
    ):
        self.regex_error_code = regex_error_code
        self.regex_job_keyword = regex_job_keyword
        self.error_code_lookup = error_code_lookup
        self.llm_classify = llm_classify
        self.retrieve = retrieve
        self.rag_answer = rag_answer
        self.write_job_record = write_job_record
        self.tau = tau
 
    def handle_query(self, query: str) -> SessionLog:
        # ── ชั้น 1: regex (ถูกสุด) ──
        code = self.regex_error_code(query)                 # [FIX] capture code
        table_row = self.error_code_lookup(code) if code else None  # merge (Design Call C)
 
        if self.regex_job_keyword(query):                   # "บันทึกงาน/จดงาน/log งาน"
            return self._log(query, RouteID.WRITE_JOB_RECORD, layer=1,
                             matched_error_code=code,
                             job_record=self.write_job_record(query))
 
        # ── ชั้น 2: LLM classify residual ──
        intent = self.llm_classify(query)
        if intent == "job":
            return self._log(query, RouteID.WRITE_JOB_RECORD, layer=2,
                             intent=intent, matched_error_code=code,
                             job_record=self.write_job_record(query))
 
        # ── ชั้น 3: knowledge path → retrieve + distance gate (Design Call A) ──
        context, distance = self.retrieve(query)
 
        # ⚠️ LOGIC: gate refuse ได้ 'เฉพาะตอนไม่มี table_row'
        # deterministic hit (error code เจอแล้ว) ไม่ควรโดน gate ปัด — และ Design Call C
        # กำหนดว่า error_code_lookup ต้อง feed rag_answer เสมอ ถ้า refuse ก่อน enrichment ก็หาย
        if distance > self.tau and table_row is None:
            return self._log(query, RouteID.OUT_OF_SCOPE_REFUSE, layer=3,
                             distance=distance, intent=intent)
 
        answer = self.rag_answer(query, context, table_row)  # merge row ถ้ามี
        return self._log(query, RouteID.RAG_ANSWER, layer=3,
                         distance=distance, intent=intent, matched_error_code=code,
                         enrichment_used=table_row is not None, content=answer)
 
    # ---- trace builder ----
    @staticmethod
    def _log(query: str, route: RouteID, *, layer: int,
             distance: Optional[float] = None,
             matched_error_code: Optional[str] = None,
             intent: Optional[str] = None,
             enrichment_used: bool = False,
             job_record: Optional[HVACJobRecord] = None,
             content: Optional[str] = None) -> SessionLog:
        return SessionLog(
            query=query,
            decision=RouteDecision(
                route=route, layer=layer, distance=distance,
                matched_error_code=matched_error_code, intent=intent,
                enrichment_used=enrichment_used,
            ),
            job_record=job_record,
            content=content,
        )
 
 
# ==========================================
# 4. ตัวอย่างการต่อของจริง (stub — Georgy เติมเอง)
# ==========================================
if __name__ == "__main__":
    import re
 
    ERROR_RE = re.compile(r"([EPF]\d{1,2}|U\d)", re.IGNORECASE)
    JOB_RE = re.compile(r"(บันทึกงาน|จดงาน|log ?งาน|บันทึกการซ่อม)")
 
    def demo_error_code(q): 
        m = ERROR_RE.search(q); return m.group(1).upper() if m else None
    def demo_job_kw(q): return bool(JOB_RE.search(q))
    def demo_lookup(code): return f"{code} = (row จากตาราง 3.1)" if code else None
    def demo_classify(q): return "knowledge"   # จริง = llama.cpp constrained JSON
    def demo_retrieve(q): return ("(context จาก retriever)", 0.35)
    def demo_rag(q, ctx, row): return f"[ตอบจาก ctx{' + '+row if row else ''}]"
    def demo_write(q): return HVACJobRecord(raw_query=q)
 
    router = CascadeRouter(
        regex_error_code=demo_error_code, regex_job_keyword=demo_job_kw,
        error_code_lookup=demo_lookup, llm_classify=demo_classify,
        retrieve=demo_retrieve, rag_answer=demo_rag, write_job_record=demo_write,
        tau=0.5,
    )
    for q in ["บันทึกงาน เปลี่ยนคอมที่บ้านลูกค้า", "E5 คืออะไร", "แอร์ไม่เย็นทำไง"]:
        log = router.handle_query(q)
        print(f"{q!r:45} -> {log.decision.route.value:20} (layer {log.decision.layer})")
