"""
Baguetteboost 백엔드 부하테스트 (Locust)

두 종류의 가상 사용자를 동시에 돌려 "몇 명까지 버티는지"를 측정한다.
  - DeviceUser  : 환자 기기. 무인증. GPS 핑(주기적) + 가끔 낙상 의심(IMU) 전송 [쓰기 경로]
  - GuardianUser: 보호자 앱/대시보드. 로그인 후 조회 API 폴링 [읽기 경로]

실행 예:
  pip install locust
  # 웹 UI (권장): 브라우저에서 사용자 수/램프 조절하며 포화점 탐색
  BASE_URL=http://<서버>:8000 locust -f locustfile.py

  # 헤드리스 (자동 램프, CI용): 200명까지 20명/초로 올려 5분 유지
  BASE_URL=http://<서버>:8000 locust -f locustfile.py --headless \
      -u 200 -r 20 -t 5m --csv=result

환경변수:
  BASE_URL          대상 서버 (기본 http://localhost:8000)
  PERSON_ID_MIN/MAX 기기 액터가 사용할 person_id 범위 (미리 seed 필요)
  GUARDIAN_PHONE_*  보호자 로그인 계정 패턴 (미리 seed 필요)
  FALL_RATE         GPS 핑 대비 낙상 전송 확률 (기본 0.02 = 2%)
"""
import os
import random
from datetime import datetime, timezone

from locust import HttpUser, task, between, events

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PERSON_ID_MIN = int(os.getenv("PERSON_ID_MIN", "1000"))
PERSON_ID_MAX = int(os.getenv("PERSON_ID_MAX", "1200"))
FALL_RATE = float(os.getenv("FALL_RATE", "0.02"))

# 툴루즈 주변(테스트 지역). 랜덤 미세 이동으로 궤적처럼 보이게.
LAT0, LNG0 = 43.6047, 1.4442


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _imu_window(n=50):
    """9채널 x n샘플. 스키마 검증(모든 채널 동일 길이)을 통과해야 함."""
    def seq():
        return [round(random.uniform(-2, 2), 4) for _ in range(n)]
    return {ch: seq() for ch in ("roll", "pitch", "yaw", "ax", "ay", "az", "wx", "wy", "wz")}


class DeviceUser(HttpUser):
    """환자 기기: 'Authorization: Device <token>' 헤더 필요.
    GPS를 자주 보내고 가끔 낙상 의심을 보낸다."""
    host = BASE_URL
    # 실기기는 20초 주기지만, 부하에서는 '적은 기기로 높은 부하'를 만들기 위해 압축.
    # 실제 주기를 재현하려면 between(18, 22) 로 바꾸고 사용자 수를 늘린다.
    wait_time = between(1, 3)

    # verify_device_token 은 'Device <token>' 형식만 확인(현재 DB 검증 TODO).
    DEVICE_HEADER = {"Authorization": "Device " + os.getenv("DEVICE_TOKEN", "loadtest-device")}

    def on_start(self):
        self.person_id = random.randint(PERSON_ID_MIN, PERSON_ID_MAX)

    @task(50)
    def send_gps(self):
        body = {
            "personId": self.person_id,
            "gps": {
                "timestamp": _now_iso(),
                "latitude": round(LAT0 + random.uniform(-0.003, 0.003), 6),
                "longitude": round(LNG0 + random.uniform(-0.003, 0.003), 6),
                "is_fall_detected": False,
                "is_wandering_detected": False,
            },
        }
        # name= 로 통계 라벨 고정(경로에 id가 없어 그대로지만 명시)
        self.client.post("/telemetry/gps", json=body, headers=self.DEVICE_HEADER,
                         name="POST /telemetry/gps")

    @task(1)
    def send_fall_suspect(self):
        if random.random() > FALL_RATE:
            return
        body = {
            "personId": self.person_id,
            "timestamp": _now_iso(),
            "imuData": _imu_window(50),
        }
        self.client.post("/telemetry/fall-suspect", json=body, headers=self.DEVICE_HEADER,
                         name="POST /telemetry/fall-suspect")


class GuardianUser(HttpUser):
    """보호자 앱: 로그인 후 대시보드 폴링(앱의 실제 폴링 패턴 근사)."""
    host = BASE_URL
    wait_time = between(2, 5)

    def on_start(self):
        # seed 된 보호자 계정으로 로그인. 계정 패턴은 환경에 맞게 조정.
        idx = random.randint(int(os.getenv("GUARDIAN_MIN", "1")),
                             int(os.getenv("GUARDIAN_MAX", "50")))
        phone = os.getenv("GUARDIAN_PHONE_FMT", "0100000{:04d}").format(idx)
        password = os.getenv("GUARDIAN_PASSWORD", "password123")
        self.token = None
        self.person_id = None
        with self.client.post("/auth/login", json={"phone": phone, "password": password},
                              name="POST /auth/login", catch_response=True) as r:
            if r.status_code == 200:
                self.token = r.json().get("accessToken") or r.json().get("access_token")
            else:
                r.failure(f"login failed {r.status_code}")
                return
        # 소유권 체크(403) 회피: 로그인한 보호자가 '자기 소유' 환자만 조회하도록
        # /persons 응답에서 실제 소유 환자 id 를 하나 고른다.
        resp = self.client.get("/persons", headers=self._auth(), name="GET /persons")
        try:
            owned = [p["id"] for p in resp.json()]
            if owned:
                self.person_id = random.choice(owned)
        except Exception:
            pass

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(5)
    def list_persons(self):
        self.client.get("/persons", headers=self._auth(), name="GET /persons")

    @task(5)
    def location(self):
        if self.person_id is None:
            return
        self.client.get(f"/persons/{self.person_id}/location", headers=self._auth(),
                        name="GET /persons/{id}/location")

    @task(4)
    def alerts(self):
        self.client.get("/alerts?limit=100", headers=self._auth(), name="GET /alerts")

    @task(3)
    def unread(self):
        self.client.get("/alerts/unread-count", headers=self._auth(),
                        name="GET /alerts/unread-count")

    @task(2)
    def history(self):
        if self.person_id is None:
            return
        frm = "2020-01-01T00:00:00Z"
        to = _now_iso()
        self.client.get(f"/persons/{self.person_id}/history?from={frm}&to={to}",
                        headers=self._auth(), name="GET /persons/{id}/history")


# ── 포화 판정 자동 로깅: 각 통계 창에서 실패율/지연을 콘솔에 남긴다 ──
@events.quitting.add_listener
def _log_summary(environment, **kw):
    stats = environment.stats.total
    print("\n=== 요약 ===")
    print(f"총 요청: {stats.num_requests}, 실패: {stats.num_failures} "
          f"({(stats.fail_ratio*100):.2f}%)")
    print(f"p50={stats.get_response_time_percentile(0.5)}ms  "
          f"p95={stats.get_response_time_percentile(0.95)}ms  "
          f"p99={stats.get_response_time_percentile(0.99)}ms")
    print(f"RPS(평균)={stats.total_rps:.1f}")
