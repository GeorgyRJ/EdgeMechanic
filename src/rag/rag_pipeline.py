"""
HVAC Assistant — RAG orchestrator (EdgeMechanic)
================================================
ผูก retriever + llama.cpp server (Qwen3-1.7B) เข้าด้วยกัน
- โหลด retriever ครั้งเดียว ใช้ซ้ำ (กัน OOM/latency บน 8GB VRAM)
- ChatML สำหรับ Qwen3 + จัดการ thinking mode (/no_think + strip <think>)
- grounded: ตอบจาก context เท่านั้น
"""
 
from __future__ import annotations
 
import re
from typing import Optional
 
import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from retriever import HVACRetriever
 
# ==========================================
# 1. Config
# ==========================================
LLM_URL = "http://127.0.0.1:8080/completion"
N_PREDICT = 512
TEMPERATURE = 0.2          # ต่ำ = ยึด context, ลด hallucination (grounded QA)
REQUEST_TIMEOUT = 120
 
# ข้อความที่ผู้ใช้เห็นเมื่อไม่มี context — เก็บที่เดียว ใช้ทั้งใน prompt และ short-circuit
NOT_FOUND_MSG = "ไม่พบข้อมูลอ้างอิงในคู่มือ"
 
# Prompt เป็นภาษาอังกฤษ แต่สั่งให้ "ตอบเป็นภาษาไทย" เพราะช่างพิมพ์/อ่านไทย
SYSTEM_PROMPT = f"""You are an AI assistant for HVAC field technicians who repair air conditioners.
Use the Context below to answer the question. Reply in Thai, concise and practical.

Rules:
- Base your answer on the Context. You may translate, summarize, and rephrase naturally in Thai.
- If asked for a procedure, answer as numbered steps.
- Keep exact values unchanged: error codes, temperatures, pressures, torque, part names.
- Preserve safety warnings from the Context when relevant.
- If the Context has no relevant information at all, reply only: "{NOT_FOUND_MSG}\""""
 
 
# ==========================================
# 2. Assistant
# ==========================================
class HVACAssistant:
    def __init__(self,
                 retriever: Optional[HVACRetriever] = None,
                 llm_url: str = LLM_URL,
                 enable_thinking: bool = False,   # ปิด think เป็น default = เร็วกว่าบน 1.7B
                 verbose: bool = True):
        # [FIX-1] โหลด retriever (BGE-M3) ครั้งเดียว ใช้ซ้ำทุก query
        self.retriever = retriever or HVACRetriever()
        self.llm_url = llm_url
        self.enable_thinking = enable_thinking
        self.verbose = verbose
 
    # ---- prompt ----
    def _build_prompt(self, query: str, context: str) -> str:
        user_message = (
            f"Context (reference material from the service manual):\n\n{context}\n\n"
            f"คำถามจากช่าง: {query}"
        )
        system = SYSTEM_PROMPT
        if not self.enable_thinking:
            system += "\n/no_think"  # คำสั่ง Qwen3 ให้ข้าม reasoning
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user_message}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
 
    # ---- llm ----
    @staticmethod
    def _strip_think(text: str) -> str:
        """[FIX] เอา <think>...</think> ของ Qwen3 ออก เผื่อโมเดลยัง emit มา."""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return text.strip()
 
    def _call_llm(self, prompt: str) -> str:
        payload = {
            "prompt": prompt,
            "n_predict": N_PREDICT,
            "temperature": TEMPERATURE,
            "stop": ["<|im_end|>", "<|im_start|>"],
            "repeat_penalty": 1.15,
        }
        try:
            resp = requests.post(self.llm_url, json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            content = resp.json().get("content", "")
            print("DEBUG RAW:", repr(content))
        except requests.exceptions.RequestException as e:
            return f"❌ เชื่อมต่อ LLM ไม่ได้: {e}"
        except ValueError as e:  # [FIX] json decode พังแยกจาก connection error
            return f"❌ LLM ตอบกลับมาไม่ใช่ JSON: {e}"
        return self._strip_think(content)
 
    # ---- main ----
    def ask(self, query: str, top_k_child: int = 8, top_k_parent: int = 3) -> str:
        self._log(f"🔍 [1/3] ค้นข้อมูลในคู่มือ: '{query}'...")
        context = self.retriever.retrieve(query, top_k_child=top_k_child, top_k_parent=top_k_parent)
        print("DEBUG CONTEXT:", context[:500])
 
        # [FIX-2] เช็ก falsiness แทนการเทียบข้อความไทยข้ามไฟล์
        if not context or context.startswith("ไม่พบ"):
            return NOT_FOUND_MSG
 
        self._log("🧠 [2/3] สร้าง prompt + ส่งให้โมเดล...")
        full_prompt = self._build_prompt(query, context)
        print("=" * 30, "FULL PROMPT LENGTH:", len(full_prompt), "=" * 30)
        print(full_prompt[-1000:])
        answer = self._call_llm(full_prompt)
        self._log("✅ [3/3] ได้คำตอบ\n")
        return answer
 
    def _log(self, msg: str):
        if self.verbose:
            print(msg)
 
    def close(self):
        self.retriever.close()
 
    def __enter__(self):
        return self
 
    def __exit__(self, *exc):
        self.close()
 
 
# ==========================================
# 3. Test Execution
# ==========================================
if __name__ == "__main__":
    questions = [
        "แอร์ Trane ไฟกระพริบ Error Code E5 ต้องเช็กอะไรก่อน?",
        "อธิบายวิธีเปลี่ยนคาปาซิเตอร์คอยล์ร้อนแบบ Step-by-step",
        "ใช้รีโมทยี่ห้อ Daikin กับแอร์ Trane ได้ไหม",  # ทดสอบ out-of-context
    ]
    # โหลด retriever ครั้งเดียวสำหรับทุกคำถาม
    with HVACAssistant() as bot:
        for q in questions:
            print("=" * 50)
            print("🤖 AI ตอบ:")
            print(bot.ask(q))
            print("=" * 50 + "\n")
