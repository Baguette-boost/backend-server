from sqlalchemy import ForeignKey, Integer, Boolean, text, func, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
from datetime import datetime

from .base import Base

# 순환 참조 방지를 위한 타입 힌팅 전용 임포트
if TYPE_CHECKING:
    from .guardian import Guardian

class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("guardians.id", ondelete="CASCADE"), nullable=False
    )
    push_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)

    # Relationships
    guardian: Mapped["Guardian"] = relationship(back_populates="settings")