from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

# --- Schema Definitions ---
class JobStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    WAITING_PARTS = "waiting_parts"
    COMPLETED = "completed"

class SessionLog(BaseModel):
    user_prompt: str
    ai_solution: str

class HVACJobRecord(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    session_log: List[SessionLog] = Field(default_factory=list)
    symptom: str
    brand: str
    status: JobStatus
    actions: List[str] = Field(default_factory=list)
    parts_used: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    notes: Optional[str] = None

# --- Test Execution ---
def run_test():
    # 1. สร้าง Log การคุยกับ AI จำลอง
    chat_log = SessionLog(
        user_prompt="แอร์ Daikin เปิดไม่ติด ไฟ Timer กระพริบ",
        ai_solution="ไฟกระพริบมักมี Error Code ซ่อนอยู่ แนะนำให้ช่างกดปุ่ม Cancel ที่รีโมทค้างไว้เพื่อเช็กโค้ดครับ"
    )

    # 2. สร้าง Record ของงานซ่อม (สังเกตว่าไม่ได้ใส่ timestamp เข้าไป ระบบจะสร้างให้เอง)
    mock_job = HVACJobRecord(
        symptom="แอร์เปิดไม่ติด ไฟ Timer กระพริบ",
        brand="Daikin",
        error_code="U4",
        actions=["เช็กสายสัญญาณระหว่างคอยล์ร้อน-เย็น", "เปลี่ยนบอร์ดคอยล์ร้อน"],
        parts_used=["Outdoor PCB Board"],
        status=JobStatus.COMPLETED,
        session_log=[chat_log],
        notes="สายสัญญาณเก่าชำรุดมาก ทำการเดินสายใหม่ให้แล้ว"
    )

    # 3. Print ออกมาเป็น JSON โดยใช้ model_dump_json() 
    # ใส่ indent=2 เพื่อให้เว้นบรรทัดและอ่านง่าย (Pretty Print)
    json_output = mock_job.model_dump_json(indent=2)
    
    print("=== HVAC Job Record JSON ===")
    print(json_output)

if __name__ == "__main__":
    run_test()