from sqlalchemy import String, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, TYPE_CHECKING

from .base import Base, TimestampMixin

# 순환 참조 방지를 위한 타입 힌팅 전용 임포트
if TYPE_CHECKING:
    from .guardian import Guardian
    from .telemetry import GpsLog
    from .alert import AlertLog

class TrackedPerson(Base, TimestampMixin):
    __tablename__ = "tracked_persons"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guardian_id: Mapped[int] = mapped_column(
        ForeignKey("guardians.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    age: Mapped[int] = mapped_column(Integer)
    blood_type: Mapped[str] = mapped_column(String(10), nullable=True)
    medical_info: Mapped[str] = mapped_column(Text, nullable=True)

    # Relationships
    guardian: Mapped["Guardian"] = relationship(back_populates="tracked_persons")
    gps_logs: Mapped[List["GpsLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["AlertLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
