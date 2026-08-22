# loadtest — 백엔드 부하테스트

서버가 동시 사용자 몇 명까지 버티는지 측정하는 Locust 기반 부하테스트.
환자 기기(무인증 GPS 핑 + 가끔 IMU 낙상) + 보호자 앱(로그인 후 조회 폴링) 혼합 부하를 건다.

## 구성
| 파일 | 설명 |
|---|---|
| `locustfile.py` | 가상 사용자 정의 (`DeviceUser` 쓰기 · `GuardianUser` 읽기) |
| `seed_loadtest.py` | 테스트 계정/환자 시드 SQL 생성기 (보호자 50 · 환자 200) |
| `monitor.sh` | 부하 중 컨테이너 CPU · DB 커넥션 15초 간격 샘플링 |
| `report.html` | 결과 시각화 보고서 |
| `results/` | 실측 산출물 (CSV · 로그) |

## 실행
```bash
# 0) 의존성 (격리 venv 권장)
python3 -m venv .venv && .venv/bin/pip install locust

# 1) 시드 — 테스트 계정·환자 생성 (id 900~949 / 1000~1199)
python3 seed_loadtest.py | docker exec -i baguetteboost-db mysql -uroot -prootpw baguetteboost_service
#    /location 404 방지용 기준 GPS 1건씩(선택): README 하단 참고

# 2) 스모크 검증 (20명·30초)
BASE_URL=http://localhost:8000 PERSON_ID_MAX=1199 \
  .venv/bin/locust -f locustfile.py --headless -u 20 -r 5 -t 30s --csv=results/smoke

# 3) 용량 램프 (400명까지 10/s, 5분) + 모니터링 병행
BASE_URL=http://localhost:8000 PERSON_ID_MAX=1199 \
  .venv/bin/locust -f locustfile.py --headless -u 400 -r 10 -t 5m --csv=results/capacity &
bash monitor.sh

# 웹 UI 로 직접 조절하려면 --headless 이하를 빼고 실행 → http://localhost:8089
```

## 환경변수
| 변수 | 기본 | 의미 |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | 대상 서버 |
| `PERSON_ID_MIN/MAX` | `1000`/`1200` | 기기 액터 person_id 범위 (시드와 일치) |
| `GUARDIAN_MIN/MAX` | `1`/`50` | 보호자 계정 인덱스 범위 |
| `DEVICE_TOKEN_FMT` | `lt_tok_{}` | 기기 `device_token` 포맷(`{}`→person_id), `seed_loadtest.py`와 매칭 |
| `FALL_RATE` | `0.02` | GPS 대비 낙상 전송 확률 |

## 핵심 결과 (400명 램프, 2026-08-22 재측정 — 최초 07-18 대비 하락)
- 처리량은 **~52 RPS**에서 정점(동시 **~210~240명**) → 400명 유지 5분 동안에도 안정화되지 않고 계속 하락(끝날 땐 ~32~40 RPS).
- 병목 = **단일 uvicorn 워커(CPU 1코어 포화)** + **DB 풀(30) 고갈**(이번엔 `QueuePool timed out` 500 에러 29건까지 발생). AI 컨테이너는 여유.
- 하락 원인: Issue #2에서 `device_token` 실제 DB 검증이 GPS/IMU 요청마다 추가돼 DB 풀 부담이 커짐. 상세·비교표는 `report.html` 참고.
- SLA p95<200ms 기준 권장 용량 **~150명**. 자세한 분석과 해결방안은 `report.html`.

> 주의: 부하생성기와 서버를 같은 호스트에서 돌리면 CPU 경쟁으로 수치가 낮게 나온다. 가능하면 부하생성기를 분리하라.
