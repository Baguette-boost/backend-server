import json
from typing import Any, Dict, List

from backend.core import redis_client

# 20초 주기 * 3회/분 * 15분 = 45개 (배회 RF 10분 윈도우 + 디바운스 여유)
GPS_BUFFER_MAXLEN = 45
# 버퍼 자체는 15분이면 충분하지만, 더 이상 핑이 안 오는(비활성/삭제) 환자의 키가
# Redis에 무한정 남지 않도록 넉넉히 TTL을 둔다(기존 인메모리 dict가 프로세스 재시작 시
# 자연히 비워지던 것의 등가물 — 새 기능이 아니라 프로세스 밖으로 옮기며 생긴 필요).
GPS_BUFFER_TTL_SECONDS = 1800


def _key(person_id: int) -> str:
    return f"gps_buffer:{person_id}"


async def add_gps_to_buffer(person_id: int, gps_data: dict) -> None:
    """수신된 GPS 데이터를 환자의 Redis 버퍼(리스트)에 추가합니다."""
    key = _key(person_id)
    pipe = redis_client.client.pipeline()
    # latitude/longitude가 Decimal이라 기본 인코더로는 직렬화가 안 됨 → float로 변환
    pipe.rpush(key, json.dumps(gps_data, default=float))
    pipe.ltrim(key, -GPS_BUFFER_MAXLEN, -1)
    pipe.expire(key, GPS_BUFFER_TTL_SECONDS)
    await pipe.execute()


async def get_patient_gps_history(person_id: int) -> List[Dict[str, Any]]:
    """AI 컨테이너로 전달할 환자의 최근 15분 GPS 기록을 리스트로 반환합니다."""
    raw = await redis_client.client.lrange(_key(person_id), 0, -1)
    return [json.loads(item) for item in raw]
