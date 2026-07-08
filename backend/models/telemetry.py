from sqlalchemy import ForeignKey, DECIMAL, Index, DateTime, func, Integer, Boolean, BigInteger, text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import TYPE_CHECKING, Optional

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
    # GPS 무효 구간에서는 AI가 lat/lng 을 보내지 않을 수 있어 nullable 로 둔다.
    latitude: Mapped[Optional[float]] = mapped_column(DECIMAL(8, 6), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(DECIMAL(9, 6), nullable=True)
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


class ImuLog(Base):
    __tablename__ = "imu_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_persons.id", ondelete="CASCADE"), nullable=False
    )
    # 디바이스가 낙상 의심을 감지한 시각 (request.timestamp)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 낙상 의심 순간의 IMU 6축 윈도우 원본: {ax, ay, az, wx, wy, wz}
    imu_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    sample_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # AI 가 예측한 낙상 여부 (판정 결과 저장 — 미판정/AI 미연결 시 NULL)
    predicted_label: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # 사람이 검수한 실제 낙상 여부 (재학습용 지도 라벨)
    true_label: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # 서버 적재 시각 (recorded_at 과 분리 — 재전송·지연 대비)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    person: Mapped["TrackedPerson"] = relationship(back_populates="imu_logs")

    # 특정 피보호자의 낙상 의심 이력을 시간순으로 조회하기 위한 복합 인덱스
    __table_args__ = (
        Index("idx_person_imu_time", "person_id", "recorded_at"),
    )
