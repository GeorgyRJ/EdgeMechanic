"""
test_llm_direct.py — แยกปัญหา: โมเดลอ่อน หรือ context รกเกิน?
ยิง context สั้น สะอาด ให้ Qwen3 โดยตรง (ไม่ผ่าน retriever)
รัน: python3 src/rag/test_llm_direct.py
"""
import requests
 
LLM_URL = "http://127.0.0.1:8080/completion"
 
SYSTEM = """You are an AI assistant for HVAC technicians. Answer using ONLY the Context.
Reply in Thai, concise. If the Context truly has no answer, reply exactly "ไม่พบข้อมูลอ้างอิงในคู่มือ"."""
 
# context สั้น สะอาด — เอาเฉพาะแถว E5 จาก failure code table
CLEAN_CTX = """Failure code table:
E0 = IDU & ODU communication failure (เช็กการเชื่อมต่อสาย IDU/ODU)
E5 = IDU/ODU mismatched failure (mismatch รุ่น IDU กับ ODU)
P0 = IPM module protection (เช็ก ODU PCB)"""
 
def call(prompt, n_predict=300):
    r = requests.post(LLM_URL, json={
        "prompt": prompt, "n_predict": n_predict,
        "temperature": 0.2, "stop": ["<|im_end|>", "<|im_start|>"],
    }, timeout=120)
    return r.json().get("content", "")
 
def build(sys, ctx, q, no_think=True):
    s = sys + ("\n/no_think" if no_think else "")
    return (f"<|im_start|>system\n{s}<|im_end|>\n"
            f"<|im_start|>user\nContext:\n{ctx}\n\nคำถาม: {q}<|im_end|>\n"
            f"<|im_start|>assistant\n")
 
q = "Error Code E5 คืออะไร ต้องเช็กอะไร"
 
print("=" * 55)
print("TEST A — context สั้นสะอาด + /no_think")
print("=" * 55)
p = build(SYSTEM, CLEAN_CTX, q, no_think=True)
print("prompt length (chars):", len(p))
print("ตอบ:", call(p), "\n")
 
print("=" * 55)
print("TEST B — context เดียวกัน แต่ 'เปิด' thinking")
print("=" * 55)
print("ตอบ:", call(build(SYSTEM, CLEAN_CTX, q, no_think=False)), "\n")
 
print("=" * 55)
print("TEST C — ไม่มี context เลย (ควรตอบ NOT_FOUND)")
print("=" * 55)
print("ตอบ:", call(build(SYSTEM, "(ไม่มีข้อมูล)", q, no_think=True)))