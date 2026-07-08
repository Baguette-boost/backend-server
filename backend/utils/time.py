"""시간대(UTC) 처리 유틸리티.

서버 전역 시간 규약:
- 하드웨어/클라이언트는 UTC 로 시간을 보낸다고 가정한다.
- DB 에는 '타임존 정보가 없는(naive) UTC' 값으로 저장한다. (컬럼이 모두 naive DateTime)
- 외부(하드웨어/클라이언트/AI)에서 시간을 받으면 혹시 몰라 UTC 로 변환한 뒤 naive 로 만들어 다룬다.
- 클라이언트로 시간을 내보낼 때는 UTC 기준 ISO 8601 문자열(뒤에 'Z')로 직렬화한다.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional, Union

from pydantic import BeforeValidator, PlainSerializer


def utcnow() -> datetime:
    """현재 시각을 naive UTC 로 반환한다 (DB 저장 규약)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_aware_utc(value: Union[str, datetime]) -> datetime:
    """문자열/naive/aware datetime 을 'aware UTC' datetime 으로 정규화한다.

    - 문자열: ISO 8601 파싱. 끝의 'Z'(Zulu) 표기는 '+00:00' 으로 치환해 수용한다.
    - aware: UTC 로 변환한다.
    - naive: 이미 UTC 로 들어왔다고 가정하고 UTC 타임존을 부여한다(방어적).
    """
    if isinstance(value, str):
        # 'Z' 는 파이썬 fromisoformat 이 버전에 따라 이해하지 못하므로 '+00:00' 으로 치환
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        # naive 값은 UTC 로 들어온 것으로 간주 (하드웨어/클라이언트 규약)
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_naive_utc(value: Union[str, datetime]) -> datetime:
    """외부 입력 시간을 DB 저장용 'naive UTC' 로 변환한다."""
    return ensure_aware_utc(value).replace(tzinfo=None)


def isoformat_utc(value: Optional[datetime]) -> Optional[str]:
    """저장된(주로 naive UTC) datetime 을 'UTC ISO 8601(...Z)' 문자열로 직렬화한다."""
    if value is None:
        return None
    # +00:00 대신 명시적 'Z' 표기로 통일
    return ensure_aware_utc(value).isoformat().replace("+00:00", "Z")


# --- Pydantic 재사용 타입 -------------------------------------------------

# 수신용: 입력 datetime/문자열을 naive UTC 로 정규화한 뒤 Pydantic 검증에 넘긴다.
IncomingUtcDatetime = Annotated[datetime, BeforeValidator(to_naive_utc)]

# 송신용: naive/aware UTC datetime 을 'Z' 표기 문자열로 직렬화한다(JSON 출력 시).
OutgoingUtcDatetime = Annotated[
    datetime,
    PlainSerializer(isoformat_utc, return_type=str, when_used="json"),
]
