"""위치(GPS) 기반 지오 계산 유틸리티.

낙상 후 부동(이동 없음) 판정을 위한 궤적 분산(회전반경) 계산에 사용한다.
저품질 GPS 의 산발적 튐(스파이크)에 강하도록, 최대 변위 대신 회전반경을 사용하고
간단한 속도 기반 이상치 필터와 신호상실 sentinel((0,0)) 제거를 함께 제공한다.
"""
import math
from typing import List, Optional, Tuple

EARTH_RADIUS_M = 6371000.0

# 사람이 낼 수 있는 현실적 상한 속도(m/s). 이보다 빠른 연속 이동은 GPS 스파이크로 간주.
# 보행 상단(~1.6m/s) 위에 헤드룸을 둔 글리치 필터 문턱(부축·빠른걸음·지터 흡수).
MAX_HUMAN_SPEED_MPS = 3.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 좌표 간 거리(m)."""
    r_lat1, r_lat2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(r_lat1) * math.cos(r_lat2) * math.sin(d_lon / 2) ** 2)
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def clean_track(rows: List[Tuple[Optional[float], Optional[float], object]]) -> List[Tuple[float, float]]:
    """(lat, lng, created_at) 시퀀스를 정제한 (lat, lng) 리스트로 변환한다.

    - None 좌표 제거
    - 신호상실 sentinel (0.0, 0.0) 제거 (하드웨어가 GPS 상실 시 찍는 값)
    - 직전 유효점 대비 속도가 MAX_HUMAN_SPEED_MPS 를 초과하는 점(스파이크) 제거
    입력은 created_at 오름차순으로 정렬되어 있다고 가정한다.
    """
    valid = []
    for lat, lng, ts in rows:
        if lat is None or lng is None:
            continue
        lat_f, lng_f = float(lat), float(lng)
        if lat_f == 0.0 and lng_f == 0.0:
            continue
        valid.append((lat_f, lng_f, ts))

    cleaned: List[Tuple[float, float]] = []
    prev = None  # (lat, lng, ts)
    for lat_f, lng_f, ts in valid:
        if prev is not None and ts is not None and prev[2] is not None:
            dt = (ts - prev[2]).total_seconds()
            if dt > 0:
                speed = haversine_m(prev[0], prev[1], lat_f, lng_f) / dt
                if speed > MAX_HUMAN_SPEED_MPS:
                    continue  # 물리적으로 불가능한 점프 → 스파이크로 보고 제거
        cleaned.append((lat_f, lng_f))
        prev = (lat_f, lng_f, ts)
    return cleaned


def radius_of_gyration_m(points: List[Tuple[float, float]]) -> float:
    """궤적의 회전반경(m): 중심(centroid)으로부터 각 점까지 거리의 RMS.

    산발적 이상치의 영향이 1/N 로 희석되어 저품질 GPS 노이즈에 강하다.
    점이 없으면 0.0 을 반환한다.
    """
    n = len(points)
    if n == 0:
        return 0.0
    c_lat = sum(p[0] for p in points) / n
    c_lng = sum(p[1] for p in points) / n
    sq_sum = sum(haversine_m(c_lat, c_lng, lat, lng) ** 2 for lat, lng in points)
    return math.sqrt(sq_sum / n)
