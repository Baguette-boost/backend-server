from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

from backend.utils.time import OutgoingUtcDatetime

class AlertResponse(BaseModel):
    id: int
    person_id: int
    alert_type: str = Field(..., alias="alertType", description="zone_exit | fall_detected | offline")
    message: str
    is_read: bool
    created_at: OutgoingUtcDatetime = Field(..., alias="createdAt")
    
    model_config = ConfigDict(
        populate_by_name = True,
        from_attributes = True
    )

class UnreadCountResponse(BaseModel):
    unread_count: int