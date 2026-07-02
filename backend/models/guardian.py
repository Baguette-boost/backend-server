from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List

from .base import Base, TimestampMixin

class Guardian(Base, TimestampMixin):
    __tablename__ = "guardians"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    phone_number: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(50))

    # 비동기 환경에서 N+1 문제 및 세션 에러를 방지하기 위해 selectin 사용
    tracked_persons: Mapped[List["TrackedPerson"]] = relationship(
        back_populates="guardian", 
        cascade="all, delete-orphan", 
        lazy="selectin"
    )
