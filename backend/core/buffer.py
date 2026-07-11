from collections import defaultdict, deque
from typing import Dict, List, Any

# 환자 ID를 Key로 하고, 최대 45개 길이를 가지는 deque를 Value로 하는 전역 버퍼
# 20초 주기 * 3회/분 * 15분 = 45개 (배회 RF 10분 윈도우 + 디바운스 여유)
patient_gps_buffer: Dict[int, deque] = defaultdict(lambda: deque(maxlen=45))

def add_gps_to_buffer(person_id: int, gps_data: dict) -> None:
    """수신된 GPS 데이터를 환자의 인메모리 버퍼에 추가합니다."""
    patient_gps_buffer[person_id].append(gps_data)

def get_patient_gps_history(person_id: int) -> List[Dict[str, Any]]:
    """AI 컨테이너로 전달할 환자의 최근 15분 GPS 기록을 리스트로 반환합니다."""
    # deque를 list로 변환하여 직렬화(JSON) 가능하도록 처리
    return list(patient_gps_buffer[person_id])