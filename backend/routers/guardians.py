from fastapi import APIRouter, Depends
from app.core.security import get_current_user

router = APIRouter(tags=["Guardians"])

@router.get("/me")
async def get_my_profile(current_guardian: dict = Depends(get_current_user)):
    """보호자 프로필 조회"""
    return {"id": current_guardian["id"], "name": current_guardian["name"], "phone": current_guardian["phone"]}