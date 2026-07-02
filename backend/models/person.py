from sqlalchemy import String, ForeignKey, Integer, Text, Boolean, DECIMAL, DateTime
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
    device_id: Mapped[str] = mapped_column(String(100))
    device_token: Mapped[str] = mapped_column(Text)
    current_battery: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    base_lat: Mapped[float] = mapped_column(DECIMAL(8, 6))
    base_lng: Mapped[float] = mapped_column(DECIMAL(9, 6))
    safe_radius: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    # Relationships
    guardian: Mapped["Guardian"] = relationship(back_populates="tracked_persons")
    gps_logs: Mapped[List["GpsLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["AlertLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
