
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cascade import (
    CascadeRouter, RouteID, HVACJobRecord,
    demo_error_code, demo_job_kw, demo_lookup, demo_rag, demo_write,
)
 
 
# ═══════════════════════════════════════════════════════════
# Fakes (dependency injection) — regex จริง + fake ที่คุมค่าได้
# ═══════════════════════════════════════════════════════════
 
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
    log = router.handle_query("/job เปลี่ยนคาปาซิเตอร์ที่บ้านลูกค้า")
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