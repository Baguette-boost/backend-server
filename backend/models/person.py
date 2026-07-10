from sqlalchemy import String, ForeignKey, Integer, Text, Boolean, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, Optional, TYPE_CHECKING

from datetime import datetime

from .base import Base, TimestampMixin

# 순환 참조 방지를 위한 타입 힌팅 전용 임포트
if TYPE_CHECKING:
    from .guardian import Guardian
    from .telemetry import GpsLog, ImuLog
    from .alert import AlertLog

class TrackedPerson(Base, TimestampMixin):
    __tablename__ = "tracked_persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guardian_id: Mapped[int] = mapped_column(
        ForeignKey("guardians.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    # 환자 전화번호(선택) — 대시보드에서 tel: 다이얼러용. 앱에서 전화 앱 실행 처리.
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    device_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    device_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_fall: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    is_wandering:Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    # 낙상 후 부동(이동 없음) 판정 에피소드 상태
    fall_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    fall_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 에피소드(윈도우) 시작 시각
    fall_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)      # 마지막 낙상 판정 시각(마감 기준)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    guardian: Mapped["Guardian"] = relationship(back_populates="tracked_persons")
    gps_logs: Mapped[List["GpsLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
    imu_logs: Mapped[List["ImuLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["AlertLog"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
