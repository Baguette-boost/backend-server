from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# --- Auth & User ---
class LoginRequest(BaseModel):
    phone: str
    password: str = Field(..., min_length=8)

class SignUpRequest(LoginRequest):
    name: str
    expo_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None

class UserSettingsUpdate(BaseModel):
    push_enabled: Optional[bool] = None
    zone_exit_alert: Optional[bool] = None
    low_battery_alert: Optional[bool] = None


class UserSettingsResponse(BaseModel):
    user_id: int
    push_enabled: bool
    zone_exit_alert: bool
    low_battery_alert: bool
    updated_at: datetime
