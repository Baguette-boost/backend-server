from sqlalchemy import String, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List

from datetime import datetime

from .base import Base, TimestampMixin

class Guardian(Base, TimestampMixin):
    __tablename__ = "guardians"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    expo_token: Mapped[str] = mapped_column(String(255), server_default=text(""), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # 비동기 환경에서 N+1 문제 및 세션 에러를 방지하기 위해 selectin 사용
    tracked_persons: Mapped[List["TrackedPerson"]] = relationship(
        back_populates="guardian", 
        cascade="all, delete-orphan", 
        lazy="selectin"
    )
