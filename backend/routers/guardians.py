from fastapi import APIRouter, Depends
from backend.core.security import get_current_user
from backend.models.guardian import Guardian

guardian_router = APIRouter(tags=["Guardians"])

@guardian_router.get("/me")
async def get_my_profile(current_guardian: Guardian = Depends(get_current_user)):
    """보호자 프로필 조회"""
    return {"id": current_guardian.id, "name": current_guardian.name, "phone": current_guardian.phone}
