from sqlalchemy import ForeignKey, DECIMAL, Index, DateTime, func, Integer, Boolean, BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import TYPE_CHECKING

from .base import Base

if TYPE_CHECKING:
    from .person import TrackedPerson

class GpsLog(Base):
    __tablename__ = "gps_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_persons.id", ondelete="CASCADE"), nullable=False
    )
    # 위경도 데이터: 소수점 6자리까지 저장 (약 0.11m 오차 범위)
    latitude: Mapped[float] = mapped_column(DECIMAL(8, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(DECIMAL(9, 6), nullable=False)
    battery: Mapped[int] = mapped_column(Integer, nullable=False)
    is_fall_detected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    is_wandering_detected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    person: Mapped["TrackedPerson"] = relationship(back_populates="gps_logs")

    # 특정 피보호자의 시간대별 경로를 빠르게 조회하기 위한 복합 인덱스
    __table_args__ = (
        Index("idx_person_gps_time", "person_id", "created_at"),
    )
