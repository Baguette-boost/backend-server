from sqlalchemy import String, ForeignKey, Boolean, Text, DateTime, func, BigInteger, Integer, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import TYPE_CHECKING

from .base import Base

if TYPE_CHECKING:
    from .person import TrackedPerson

class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_persons.id", ondelete="CASCADE"), nullable=False
    )
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False) # 'wandering' | 'fall_detected' | 'offline'
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    person: Mapped["TrackedPerson"] = relationship(back_populates="alerts")

    # 복합 인덱스 정의
    __table_args__ = (
        Index("idx_person_alert_time", "person_id", "created_at"),
    )