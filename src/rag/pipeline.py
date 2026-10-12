"""
pipeline.py — ประกอบ cascade จริง (EdgeMechanic Week 3 Block 3)
================================================================
เสียบของจริงเข้า CascadeRouter แทน demo_* :
  retriever (distance) + error_code_lookup (SQLite) + rag_answer (llama.cpp)

หัวใจ: error_code_lookup ดึง row เป๊ะจาก SQLite → merge เข้า context
→ แก้ E5 hallucination (โมเดลไม่ต้องเดา row จากตารางยาว ๆ)

รัน (ต้อง start llama-server port 8080 ก่อน):
    python3 src/rag/pipeline.py
"""
from __future__ import annotations

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from cascade import (
    CascadeRouter, HVACJobRecord,
    error_code_lookup,                 # SQLite lookup จริง (COLLATE NOCASE)
    ERROR_RE, JOB_RE,                  # regex จริง
    demo_write,                        # write_job_record ยัง placeholder (Week 4+)
)
from retriever import HVACRetriever


# ==========================================
# 1. Config (ยืมจาก rag_pipeline.py)
# ==========================================
LLM_URL = "http://127.0.0.1:8080/completion"
N_PREDICT = 512
TEMPERATURE = 0.2
REQUEST_TIMEOUT = 120
NOT_FOUND_MSG = "ไม่พบข้อมูลอ้างอิงในคู่มือ"

SYSTEM_PROMPT = f"""You are an AI assistant for HVAC field technicians who repair air conditioners.
Use the Context below to answer the question. Reply in Thai, concise and practical.

Rules:
- Base your answer on the Context. You may translate, summarize, and rephrase naturally in Thai.
- If asked for a procedure, answer as numbered steps.
- Keep exact values unchanged: error codes, temperatures, pressures, torque, part names.
- Preserve safety warnings from the Context when relevant.
- If the Context has no relevant information at all, reply only: "{NOT_FOUND_MSG}\""""


# ==========================================
# 2. Adapters — wrap ของจริงให้เข้า cascade signature
# ==========================================
def make_regex_error_code():
    """query -> code | None  (regex จริง จาก cascade)"""
    def fn(q: str):
        m = ERROR_RE.search(q)
        return m.group(1).upper() if m else None
    return fn


def make_regex_job_keyword():
    """query -> bool  (/job หรือ /จด)"""
    def fn(q: str):
        return bool(JOB_RE.search(q))
    return fn


def make_retrieve(retriever: HVACRetriever):
    """query -> (context, distance)  — wrap param เกินออก (cascade ส่งแค่ query)"""
    def fn(q: str):
        return retriever.retrieve(q)   # คืน tuple แล้ว (เราเพิ่งแก้)
    return fn


def _call_llm(prompt: str) -> str:
    payload = {
        "prompt": prompt,
        "n_predict": N_PREDICT,
        "temperature": TEMPERATURE,
        "stop": ["<|im_end|>", "<|im_start|>"],
        "repeat_penalty": 1.15,
    }
    try:
        resp = requests.post(LLM_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        content = resp.json().get("content", "")
    except requests.exceptions.RequestException as e:
        return f"❌ เชื่อมต่อ LLM ไม่ได้: {e}"
    except ValueError as e:
        return f"❌ LLM ตอบกลับมาไม่ใช่ JSON: {e}"
    # strip <think> ของ Qwen3
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def make_rag_answer():
    """(query, context, table_row) -> answer
    *** หัวใจ Block 3: merge table_row เข้า context ก่อนส่ง LLM ***
    cascade retrieve มาแล้ว เราไม่ retrieve ซ้ำ (ต่างจาก HVACAssistant.ask เดิม)
    """
    def fn(query: str, context: str, table_row):
        # [MERGE] ถ้ามี error code row → pin ไว้หน้าสุด ให้โมเดลเห็นชัด
        if table_row:
            context = (
                f"[ERROR CODE ที่ระบุตรง ๆ จากตาราง]\n{table_row}\n\n"
                f"[ข้อมูลเพิ่มเติมจากคู่มือ]\n{context}"
            )

        if not context or context.startswith("ไม่พบ"):
            return NOT_FOUND_MSG

        system = SYSTEM_PROMPT + "\n/no_think"
        user_msg = (
            f"Context (reference material from the service manual):\n\n{context}\n\n"
            f"คำถามจากช่าง: {query}"
        )
        prompt = (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user_msg}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        return _call_llm(prompt)
    return fn


# ==========================================
# 3. ประกอบ cascade จริง
# ==========================================
def build_pipeline(tau: float = 0.5) -> CascadeRouter:
    retriever = HVACRetriever()        # โหลด BGE-M3 ครั้งเดียว
    return CascadeRouter(
        regex_error_code=make_regex_error_code(),
        regex_job_keyword=make_regex_job_keyword(),
        error_code_lookup=error_code_lookup,        # SQLite จริง!
        llm_classify=lambda q: "knowledge",         # placeholder — job classify ไว้ทีหลัง
        retrieve=make_retrieve(retriever),
        rag_answer=make_rag_answer(),
        write_job_record=demo_write,                # placeholder (Week 4+)
        tau=tau,
    )


# ==========================================
# 4. Test — 3 query เดิม (ดู E5 hallucination หายไหม)
# ==========================================
if __name__ == "__main__":
    bot = build_pipeline()
    questions = [
        "แอร์ Trane ไฟกระพริบ Error Code E5 ต้องเช็กอะไรก่อน?",   # เคย hallucinate
        "E0 แก้ยังไง",                                            # มีทั้งตาราง + วิธีแก้
        "ใช้รีโมทยี่ห้อ Daikin กับแอร์ Trane ได้ไหม",            # out-of-scope
    ]
    for q in questions:
        print("=" * 60)
        log = bot.handle_query(q)
        d = log.decision
        print(f"Q: {q}")
        print(f"→ route={d.route.value} | layer={d.layer} | "
              f"dist={d.distance} | enrich={d.enrichment_used} | code={d.matched_error_code}")
        print(f"→ ตอบ: {log.content}")
        print("=" * 60 + "\n")