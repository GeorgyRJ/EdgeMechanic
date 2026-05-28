from pydantic import BaseModel , Field
from datetime import datetime 
from enum import Enum
from typing import List , Optional

class JobStatus (str,Enum):
    IN_PROGRESS = "in_progress"
    WAITING_PARTS = "waiting_parts"
    COMPLETED = "completed"

class Sessionlog (BaseModel):
    user_prompt : str 
    ai_solution : str 

class HVACJobRecord(BaseModel):
    timestamp : datetime = Field(
        default_factory = datetime.now,
        description= "Timestamp when the job was recorded"
    )

    session_log : list[Sessionlog] = Field(
        default_factory = list , 
        description ="Conversation history between technician and AI"
    )

    symptom : str = Field (description="Primary symptom reported by technician")
    brand : str = Field (description="HVAC unit brand/manufacturer")
    status : JobStatus = Field (description="Current job status")

    actions: List[str] = Field(
        default_factory=list, 
        description="Action Taken"
    )
    parts_used: List[str] = Field(
        default_factory=list, 
        description="Parts Used"
    )
    
    # Optional Fields
    error_code: Optional[str] = Field(
        default=None, 
        description="Error Code (Optional but highly recommended)"
    )
    notes: Optional[str] = Field(
        default=None, 
        description=" Note : "
    )