from fastapi import APIRouter, Depends
from backend.core.security import get_current_user
from backend.models.guardian import Guardian
from backend.models.setting import UserSettings
from backend.schemas.guardian import UserUpdate, UserSettingsUpdate, UserSettingsResponse
from backend.database import get_db
from sqlalchemy import select, update
from typing import Annotated
from sqlalchemy.ext.asyncio import AsyncSession

guardian_router = APIRouter(tags=["Guardians"])

@guardian_router.get("/me")
async def get_my_profile(current_guardian: Guardian = Depends(get_current_user)):
    """보호자 프로필 조회"""
    return {"id": current_guardian.id, "name": current_guardian.name, "phone": current_guardian.phone}

@guardian_router.patch("/me", response_model=UserUpdate)
async def update_profile(
    payload: UserUpdate, 
    current_user: Annotated[Guardian, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db)
):
    stmt = update(Guardian).where(Guardian.id == current_user.id).values(**payload.model_dump(exclude_unset=True))
    await db.execute(stmt)
    await db.commit()
    return payload

@guardian_router.get("/me/settings", response_model=UserSettingsResponse)
async def get_settings(current_user: Annotated[Guardian, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings

@guardian_router.patch("/me/settings", response_model=UserSettingsResponse)
async def update_settings(
    payload: UserSettingsUpdate,
    current_user: Annotated[Guardian, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, key, value)

    await db.commit()
    await db.refresh(settings)
    return settings