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

## 핵심 결과 (400명 램프, 2026-08-23 재측정 — Issue #4 2단계: gunicorn 4워커 + Redis)
- 08-22(단일 워커) 대비 처리량 **52→75 RPS(평균, 램프 피크 ~90 RPS)**, 400명 p95 **15s→5.1s**, `QueuePool timed out` 500 에러는 **29건→0건**.
- 조치: 단일 uvicorn → **gunicorn 4워커**로 전환(호스트 4 vCPU 활용). 선행 필요했던 스케줄러 중복 실행·GPS 인메모리 버퍼·WebSocket 알림(전부 워커별 로컬 메모리라 멀티워커에서 깨짐)은 **Redis**로 공유해 해결(버퍼는 리스트, 알림은 Pub/Sub 팬아웃, 스케줄러는 별도 `baguetteboost-scheduler` 프로세스로 분리).
- DB 커넥션 풀은 이번 단계에서 건드리지 않았다(워커마다 독립 풀이라 4워커 전환만으로 총 여력이 자동 4배). **먼저 풀 크기만 올려본 실험은 오히려 악화되어(29 RPS로 하락) 원복** — DB가 아니라 CPU가 병목이었다는 걸 확인했다(상세는 Issue #4 코멘트/`report.html`).
- 실패율은 0.27%→**1.81%**로 늘었지만 원인은 DB 타임아웃이 아니라 `RemoteDisconnected`(연결 끊김) — co-located 테스트 환경 특성으로 추정(확정 아님).
- SLA p95<200ms 기준 권장 용량은 **~90명으로 08-22와 동일**(개선 안 됨) — 이번 조치는 "과부하에서 완전히 무너지지 않게" 만든 것이지 저부하 SLA 상한을 올린 게 아니다. 자세한 분석과 다음 단계(배회 detect 분리 등)는 `report.html` 참고.

> 주의: 부하생성기와 서버를 같은 호스트에서 돌리면 CPU 경쟁으로 수치가 낮게 나온다. 가능하면 부하생성기를 분리하라.
