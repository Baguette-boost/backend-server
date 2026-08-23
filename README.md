# Baguette Boost / Voost — 배회·낙상 감지 백엔드

치매 등으로 보호가 필요한 대상이 스마트 조끼(ESP32 + IMU + GPS)를 착용하면, 서버가 **배회**와
**낙상**을 실시간으로 감지해 보호자 앱에 즉시 알리는 시스템의 백엔드 레포입니다.
ICCAS 2026 발표 프로젝트(팀명 Baguette Boost / 서비스명 Voost)이며, 이 레포는 그중 **백엔드
(FastAPI + MySQL + 2개의 AI 추론 컨테이너 오케스트레이션)** 를 담당합니다.

> AI 모델(TCN 낙상 분류기, 환자별 Random Forest 배회 분류기)의 학습·연구는 팀원이 별도로
> 진행하며, 이 레포는 그 모델을 **서빙·통합**하는 백엔드입니다.

## 목차
1. [프로젝트 개요](#프로젝트-개요)
2. [디렉토리 구조](#디렉토리-구조)
3. [빠른 시작](#빠른-시작)
4. [아키텍처 & 기술 스택](#아키텍처--기술-스택)
5. [API 요약](#api-요약)
6. [핵심 트러블슈팅 3가지](#핵심-트러블슈팅-3가지)
7. [한계와 다음 단계](#한계와-다음-단계)

---

## 프로젝트 개요

| 항목 | 내용 |
|---|---|
| 대상 사용자 | 치매 등 인지저하 환자(피보호자)의 보호자 |
| 하드웨어 | ESP32 기반 스마트 조끼 — IMU(가속도·자이로) + GPS 모듈 |
| 감지 대상 | **배회**(GPS 궤적 이상), **낙상**(IMU 급변) |
| 알림 채널 | 보호자 앱 WebSocket(실시간 대시보드) + Expo Push(모바일 푸시) |
| 배포 단위 | Docker Compose 6-컨테이너: MySQL · Redis · Backend(FastAPI, gunicorn 4워커) · Scheduler(독립 프로세스) · AI-낙상(TCN) · AI-배회(환자별 RF) |

### 전체 데이터 흐름

```
ESP32 조끼
  ├─ 20초 주기 GPS ──► POST /telemetry/gps ──► Redis 버퍼(15분) ──► AI-배회(RF) ──► 배회 시 알림
  └─ 가속도 임계치 초과 시
     직전10초+직후2초 IMU ──► POST /telemetry/fall-suspect ──► AI-낙상(TCN) ──► 낙상 시 알림

보호자 앱
  ├─ WebSocket /realtime ──► 실시간 위치/상태/알림 수신
  └─ REST(JWT) ────────────► 환자 목록·경로·알림 이력 조회, 알림 설정, 수동 상태 정상화
```

GPS 경로는 **배회만**, IMU 경로는 **낙상만** 판정합니다(두 파이프라인은 서로 섞이지 않습니다).
낙상은 감지 즉시 끝나는 이벤트가 아니라, 이후 "부동 상태(움직이지 않음)"를 스케줄러가 계속
추적해 회복 시 자동으로 경보를 해제합니다.

---

## 디렉토리 구조

```
backend-server/
├── backend/                  # FastAPI 애플리케이션 (이 레포의 핵심, gunicorn 4워커로 서빙)
│   ├── main.py                #   앱 진입점, lifespan(Redis/AI 클라이언트 기동 — 스케줄러는 없음)
│   ├── scheduler_main.py       #   독립 스케줄러 프로세스 진입점(baguetteboost-scheduler 서비스)
│   ├── config.py               #   pydantic-settings 기반 환경설정 단일 소스
│   ├── database.py             #   비동기 SQLAlchemy 엔진/세션 팩토리
│   ├── routers/                 #   HTTP 엔드포인트 (auth, persons, telemetry, alerts, guardians, realtime)
│   ├── services/                 #   ai_client(AI 컨테이너 호출), notification_service(WS+Push 통합 발송)
│   ├── core/                      #   buffer(GPS 버퍼, Redis 리스트), scheduler(APScheduler 잡),
│   │                               #   security(인증/인가), websocket(연결 매니저+Redis Pub/Sub 팬아웃),
│   │                               #   redis_client(공유 Redis 클라이언트)
│   ├── models/                     #   SQLAlchemy ORM 모델
│   ├── schemas/                     #   Pydantic 요청/응답 스키마
│   └── utils/                        #   time(UTC 변환 유틸), geo(회전반경 계산)
│
├── ai-serve/                  # 낙상 전용 추론 서버 (우리가 작성한 wrapper)
│   └── predict_server.py      #   팀원의 TCN 모델을 로드해 POST /predict 제공
│
├── wandering_detector/        # 배회 전용 추론 서버 (팀원 원본, 무수정 — gitignore 대상)
├── ai-model-release/          # 낙상 모델 학습 스크립트/체크포인트 (팀원 원본 — gitignore 대상)
│
├── database/
│   └── init.sql               # 스키마 정의 (컨테이너 최초 기동 시 1회 실행)
│
├── loadtest/                  # Locust 기반 부하테스트 도구 + 결과 리포트
├── tests/                     # pytest (Expo Push 등 유닛 테스트)
├── scripts/                   # 보조 스크립트 (더미 GPS 트랙 생성기 등)
├── docker-compose.yml         # 6-컨테이너 오케스트레이션(db·redis·backend·scheduler·ai-fall·ai-wander)
├── API.md                     # API 상세 레퍼런스 (실제 구현 기준)
├── API.prev.md                # 예전 기획 단계 API 문서 (참고용 — 구현과 다른 부분 많음)
└── README.md                  # 이 문서
```

---

## 빠른 시작

```bash
# 전체 스택 기동 (MySQL + Backend + AI-낙상 + AI-배회)
docker compose up -d --build

# 백엔드만 재빌드/재시작 (코드만 바뀐 경우)
docker compose up -d --build baguetteboost-backend

# 로그 확인
docker logs baguetteboost-backend -f

# 헬스체크
curl http://localhost:8000/health
```

부하테스트는 [`loadtest/README.md`](loadtest/README.md)를 참고하세요.

---

## 아키텍처 & 기술 스택

### 왜 6-컨테이너로 나눴는가

```
┌─────────────────┐      ┌──────────────────────┐
│  baguetteboost-  │──┬──►│ baguetteboost-ai-fall │  TCN(PyTorch), personId별 아니라
│  backend         │  │   │  POST /predict        │  전역 모델 1개 (Case 3 참고)
│  (FastAPI,       │  │   └──────────────────────┘
│   gunicorn 4워커) │  │
│                  │  └──►┌──────────────────────┐
│                  │      │ baguetteboost-ai-wander│  환자별 RandomForest(.joblib),
│                  │      │  POST /users/{id}/detect│ /data 볼륨에 영속
│                  │      └──────────────────────┘
└──┬───────────┬───┘
   │           │
   ▼           ▼
┌─────────┐ ┌───────────────────┐      ┌────────────────────────┐
│ baguette│ │ baguetteboost-redis│◄────►│ baguetteboost-scheduler │
│ boost-db│ │ GPS 버퍼(리스트) +  │      │ 독립 프로세스(HTTP 없음) │
│ MySQL 8 │ │ WS 알림 Pub/Sub    │      │ 하트비트·낙상판정·배회enroll│
└─────────┘ └───────────────────┘      └────────────────────────┘
```

4워커로 가면서 프로세스 메모리에만 있던 상태(GPS 버퍼, 스케줄러, WebSocket 연결 목록)가
워커마다 갈라지는 문제가 생겨, Redis를 공유 저장소로 두고 스케줄러를 아예 별도 프로세스로
뺐습니다(자세한 배경은 [트러블슈팅 3](#3-부하테스트로-드러난-db-커넥션-풀-고갈) 참고).

- **AI 컨테이너를 분리한 이유**: 팀원이 소유한 모델 코드(`wandering_detector/`,
  `ai-model-release/`)를 백엔드가 **무수정**으로 그대로 실행하기 위해서입니다. 백엔드가
  직접 `import`해서 쓰면 팀원의 의존성(PyTorch/scikit-learn 버전 등)과 백엔드 자체의
  의존성이 한 프로세스에 뒤섞이고, 팀원 코드가 바뀔 때마다 백엔드 배포가 얽히게 됩니다.
  대신 HTTP 계약(`POST /predict`, `POST /users/{id}/detect`)만 정의해 컨테이너 경계로
  분리했습니다.
- **낙상과 배회를 별도 컨테이너·별도 모델로 나눈 이유**: 두 문제의 데이터 성격이 다릅니다.
  낙상은 "전역적으로 통하는 동작 패턴"(급격한 가속도 변화)이라 모두에게 같은 모델을 적용해도
  되지만, 배회는 "이 사람의 평소 동선과 얼마나 다른가"가 핵심이라 환자마다 별도로 학습해야
  의미가 있습니다. 이 비대칭성이 서빙 방식(전역 1개 vs. 환자별 N개)에도 그대로 반영되어
  있습니다.

### 기술 스택 선택 이유

| 영역 | 선택 | 이유 |
|---|---|---|
| 웹 프레임워크 | **FastAPI** | 센서 데이터가 초당 다건 유입되는 I/O-bound 워크로드라 `async def` 네이티브 지원이 중요했고, Pydantic 기반 요청 검증(IMU 9채널 길이 일치 등)을 라우터 코드 없이 스키마로 강제할 수 있음 |
| DB 드라이버 | **SQLAlchemy(async) + aiomysql** | AI 추론 대기(최대 5초 타임아웃) 동안 커넥션을 물고 있지 않도록 비동기로 처리해야 커넥션 풀이 고갈되지 않음 — 이 원칙이 지켜지지 않았을 때 벌어진 일은 [트러블슈팅 3](#3-부하테스트로-드러난-db-커넥션-풀-고갈) 참고 |
| DB | **MySQL 8** | GPS/IMU 시계열이 `(person_id, created_at)` 복합 인덱스로 조회되는 패턴이 명확해 관계형 DB로 충분했고, 팀 내 운영 경험이 있어 선택 |
| 스케줄링 | **APScheduler**(독립 프로세스) | 하트비트(오프라인 판정)·낙상 부동판정(1분 주기)·배회 개인모델 자동 enroll(1일 1회) 등 가벼운 백그라운드 폴링이라 Celery 같은 별도 큐는 과했지만, 멀티워커 전환 후엔 "가벼우니 프로세스에 얹어둔다"가 아니라 워커 수와 무관하게 정확히 1개만 돌아야 해서 `backend/scheduler_main.py`로 분리(자세한 배경은 [트러블슈팅 3](#3-부하테스트로-드러난-db-커넥션-풀-고갈)) |
| 공유 상태 | **Redis**(GPS 버퍼 + WebSocket Pub/Sub) | gunicorn 멀티워커 전환 시 GPS 버퍼·WebSocket 연결 목록이 프로세스(워커)마다 갈라지는 문제가 생겨 도입. 버퍼는 리스트로, 워커 간 알림 팬아웃은 Pub/Sub으로 — 둘 다 원래 있던 기능을 프로세스 밖으로 옮긴 것이라 새 요구사항이 생긴 건 아님 |
| 실시간 알림 | **WebSocket(대시보드) + Expo Push(모바일)** | 보호자가 앱을 보고 있으면 WS로 즉시, 안 보고 있으면 푸시로 깨워야 해서 두 채널을 병행. 하나의 이벤트(`NotificationService._notify`)에서 두 채널을 함께 쏘도록 통일 — 그렇지 않았을 때 실제로 offline 알림이 이중 발송되는 버그가 있었고, 이 통합 과정에서 함께 수정([`a8cdbda`](https://github.com/Baguette-boost/backend-server/commit/a8cdbda)) |
| 컨테이너 오케스트레이션 | **Docker Compose** | 단일 서버에 배포되는 규모(대회/프로토타입)라 Kubernetes는 과했고, `depends_on` + `healthcheck`만으로 기동 순서(DB → 백엔드 → AI)를 충분히 보장 |

---

## API 요약

전체 스펙은 [`API.md`](API.md)에 있습니다 — 실제 라우터/스키마 코드(`backend/routers/`,
`backend/schemas/`)를 기준으로 작성돼 있어 그대로 신뢰할 수 있습니다. (예전에 클라이언트
기획 관점으로 먼저 쓰였던 문서는 구현과 여러 곳이 어긋나 있어 [`API.prev.md`](API.prev.md)로
이름을 바꿔 참고용으로만 남겨뒀습니다 — 새로 통합하실 때는 보지 않으셔도 됩니다.)

### 공통 규약
- 인증: 보호자 앱 → `Authorization: Bearer <access_token>` (JWT), 디바이스 → `Authorization: Device <device_token>`
- 시간: 모든 송수신은 ISO 8601 UTC(`...Z`), DB는 naive UTC로 저장 (`backend/utils/time.py`)
- 에러: 라우터마다 `HTTPException`을 직접 반환하는 방식이라 공통 에러 포맷이 아직 없습니다
  (`{"detail": "..."}` 형태, Pydantic 검증 실패는 FastAPI 기본 `422` 배열 포맷). 상태 코드는
  `400`(검증 실패) · `401`(미인증) · `403`(권한 없음) · `404`(리소스 없음) · `409`(중복) ·
  `422`(스키마 검증) · `500`(서버 오류).
- 페이지네이션은 없습니다 — `GET /alerts`/`GET /persons/{id}/history`는 `limit`만 지원.

### 핵심 엔드포인트
| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/auth/signup` · `/auth/login` | 회원가입 / 로그인 → `{access_token, refresh_token, token_type}` |
| POST | `/auth/refresh` | 헤더 `refresh-token`으로 토큰 재발급(회전) |
| GET · PATCH | `/me`, `/me/settings` | 보호자 프로필 / 알림 설정 |
| POST | `/persons` | 환자 등록·디바이스 페어링(클라이언트가 `device_token` 전송 → 서버가 `device_id` 생성) |
| GET · PATCH · DELETE | `/persons`, `/persons/{id}` | 환자 CRUD |
| POST | `/persons/verify` | 기기 토큰 유효성 검증(페어링 전, 디바이스가 호출) |
| GET | `/persons/{id}/location`, `/persons/{id}/history` | 최신 위치/상태, 경로 히스토리 |
| PATCH | `/persons/{id}/status` | 낙상/배회 상태 수동 정상화(해제) — 성공 시 WS `status_update` 발행 |
| POST | `/persons/{id}/wandering/re-enroll` | 배회 개인모델 수동 재학습 |
| POST | `/telemetry/gps`, `/telemetry/fall-suspect` | (Device 인증) GPS/IMU 수신 → 배회/낙상 AI 위임 |
| GET · PATCH · POST | `/alerts`, `/alerts/{id}/read`, `/alerts/read-all` | 알림 이력·읽음 처리 |
| WS | `/realtime` | 헤더 `accessToken`으로 인증, 실시간 `alert`/`status_update` 이벤트 수신 |

```jsonc
// POST /auth/login → 200
{ "access_token": "eyJhbGciOi...", "refresh_token": "n3F9x...", "token_type": "Bearer" }
```
```jsonc
// POST /telemetry/gps  (Authorization: Device <device_token>)  → 201
{ "personId": 1, "gps": { "latitude": 37.50123, "longitude": 127.03645, "timestamp": "2026-08-22T00:41:00Z" } }
```

인증 실패(`401`) 또는 `personId`가 토큰 소유자와 다른 사칭 시도(`403`)는 텔레메트리 경로에서
즉시 차단됩니다([트러블슈팅 1](#1-devicetoken-사칭-차단) 참고).

### 통합 시 유의할 점(자주 놓치는 것들)
- **푸시 토큰**: `expo_token`은 회원가입 시 1회만 저장되고 갱신/삭제 엔드포인트가 없습니다.
  앱 재설치 등으로 토큰이 바뀌면 현재는 DB를 직접 갱신하는 것 외에 방법이 없습니다.
- **알림 읽음 처리**: 전체 읽음(`POST /alerts/read-all`)은 내 환자로 범위가 한정되지만,
  단건 읽음(`PATCH /alerts/{id}/read`)은 소유권 검증 없이 `alert_id`만으로 갱신됩니다(비대칭).
- **WebSocket**: 실제로 발행되는 이벤트는 `alert`와 `status_update` 두 종류뿐입니다. GPS
  위치 자체는 WS로 실시간 푸시되지 않고 `GET /persons/{id}/location` 폴링으로만 조회합니다.
- **대시보드 요약**: 집계 엔드포인트가 없습니다 — `GET /persons` + 환자별
  `GET /persons/{id}/location`(N+1) + `GET /alerts/unread-count` 조합으로 클라이언트가 직접
  구성해야 합니다.

자세한 요청/응답 예시, 데이터 모델, 전체 엔드포인트 목록은 [`API.md`](API.md)를 참고하세요.

---

## 핵심 트러블슈팅 3가지

### 1. device_token 사칭 차단

**문제 상황**
디바이스 인증(`Authorization: Device <token>`)이 라우터에 걸려 있긴 했지만, 실제로는 헤더
형식만 확인할 뿐 DB 대조가 없었고, 반환값도 어디서도 쓰이지 않는 죽은 값이었습니다. 즉 임의의
문자열 토큰으로 **아무 `personId`의 GPS/IMU 데이터든 주입**할 수 있는 상태였습니다.

**원인 분석**
`Depends(verify_device_token)`이 라우터 레벨 `dependencies=[...]`로 걸려 있어 반환값을 라우트
핸들러가 받을 구조 자체가 아니었습니다. 그러다 보니 "토큰이 유효한가"는 확인해도 "이 토큰의
주인이 요청 바디에 적힌 그 `personId`가 맞는가"는 애초에 검증할 방법이 없었습니다. 인증
(authentication)과 인가(authorization)를 분리해서 봐야 한다는 걸 놓친 설계였습니다.

**해결책**
1. 이미 `/persons/verify` 전용으로 있던 DB 조회 로직을 `_find_person_by_device_token()`
   공용 헬퍼로 추출해 재사용(`backend/core/security.py`).
2. `verify_device_token`을 라우터 레벨 의존성에서 **각 라우트 파라미터**로 옮겨
   `device_person_id: int = Depends(verify_device_token)` 형태로 실제 캡처.
3. `device_person_id != request.personId`이면 `403 Forbidden`으로 즉시 차단
   (`backend/routers/telemetry.py`).

```python
@telemetry_router.post("/gps", status_code=status.HTTP_201_CREATED)
async def receive_gps(
    request: GPSRequest,
    background_tasks: BackgroundTasks,
    device_person_id: int = Depends(verify_device_token),   # 반환값을 실제로 캡처
    db: AsyncSession = Depends(get_db)
):
    if device_person_id != request.personId:                 # 사칭 차단
        raise HTTPException(status_code=403, detail="Device token does not match personId.")
```

**성과 / 배운 점**
- 실제 스택에서 4가지 케이스(정상 `201`/`202`, 잘못된 토큰 `401`, personId 불일치 `403`,
  기존 `/persons/verify` `400` 회귀 없음)를 직접 curl로 검증.
- 부수적으로 `loadtest/locustfile.py`가 전체 가상 사용자 공용 토큰 1개를 쓰고 있어 이 수정
  하나로 부하테스트 전체가 401로 깨지는 것도 함께 발견 — 부하테스트 도구도 환자별 고유 토큰
  패턴으로 맞춰 수정했습니다.
- **"인증 의존성이 걸려 있다"와 "그 인증 결과로 인가까지 하고 있다"는 다른 문제**라는 걸
  코드 리뷰 습관으로 남긴 케이스입니다.

### 2. Production JWT 실서명 발급/검증 정합화

**문제 상황**
`.env`의 `DEBUG_MODE`를 `false`로 바꿔 프로덕션 인증 경로를 켜면 로그인 직후부터 모든 인증이
깨졌습니다.

**원인 분석**
- `create_jwt_tokens()`가 `DEBUG_MODE`와 무관하게 **항상** 더미 토큰(`"{id}_ACCESS"`)을
  발급하고 있었는데, `get_current_user()`의 production 분기는 이 더미 문자열을 실제
  `jwt.decode()`로 검증하려다 항상 실패했습니다.
- `verify_refresh_token()`은 애초에 `DEBUG_MODE` 분기 자체가 없어 실제 JWT를 거부했습니다.
- WebSocket(`/realtime`)은 헤더 문자열을 검증 없이 그대로 `guardian_id`로 캐스팅하고 있었습니다.

**해결책**
- Access token은 서명된 JWT(HS256, `sub=phone`, `exp` 60분)로, refresh token은 JWT로 만들지
  않고 **불투명 랜덤 문자열**(`secrets.token_urlsafe(32)`)로 유지 — 이미 `Guardian.refresh_token`
  컬럼과 정확히 일치하는지 대조해 무효화하는 구조가 있었기 때문에, 그 패턴을 그대로 따르는 게
  가장 적은 변경이었습니다.
- REST(`get_current_user`)와 WebSocket이 같은 검증 로직을 두 번 짜지 않도록
  `resolve_guardian_from_access_token()`으로 공용화(`backend/core/security.py`).
- `DEBUG_MODE`는 걷어내지 않고 유지 — `.env` 기본값(`true`)에 로그인/회원가입 평문 비밀번호
  분기와 부하테스트가 이미 의존하고 있어, "꺼져 있던 production 분기를 실제로 켠다"는 최소
  변경 쪽을 선택했습니다.

```python
async def resolve_guardian_from_access_token(token: str, db: AsyncSession) -> Guardian:
    if DEBUG_MODE:
        ...  # 더미 토큰: DB access_token 컬럼과 문자열 대조
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])   # production: 실제 서명 검증
    guardian_phone = payload.get("sub")
    ...
```

**성과 / 배운 점**
- 검증 과정에서 `POST /auth/refresh`가 새로 발급한 토큰을 DB에 저장하지 않는 버그를 추가로
  발견해 함께 수정했습니다. DEBUG 모드는 더미 토큰이 결정적(같은 id → 같은 문자열)이라 이
  버그가 드러나지 않았지만, production에서는 refresh token이 매번 랜덤으로 회전되기 때문에
  재발급 직후 다음 refresh가 항상 실패하는 상태였습니다.
- DEBUG_MODE=true/false 두 모드를 모두 실제로 기동해서 회귀 테스트했고(위조 토큰 401,
  refresh 회전·재사용 차단, WebSocket 연결/거부 확인), **"평소에 안 쓰는 분기는 죽은
  코드처럼 방치될 수 있다"**는 게 이번 케이스의 핵심 교훈이었습니다.

### 3. 부하테스트로 드러난 DB 커넥션 풀 고갈

**문제 상황**
"서버가 동시 사용자 몇 명까지 버티는지" 확인하기 위한 Locust 부하테스트(GPS/IMU 쓰기 +
보호자 앱 조회 폴링 혼합)를 트러블슈팅 1의 device_token 검증 도입 **이전/이후**로 비교
측정했더니, 처리량이 **77 RPS → 52 RPS**로, SLA(p95 < 200ms) 기준 권장 동시 사용자 수가
**~150명 → ~90명**으로 하락했습니다. 400명 동시 부하 유지 구간에서는 이전과 달리 안정화되지
않고 5분 내내 계속 처리량이 떨어졌고, `sqlalchemy.exc.TimeoutError: QueuePool ... timed out`
(→ HTTP `500`)이 새로 29건 관측됐습니다.

**원인 분석**
GPS/IMU 텔레메트리는 서버에서 가장 트래픽이 큰 경로인데, 트러블슈팅 1의 수정으로 이 경로마다
`device_token` DB 조회가 **필수로 추가**됐습니다. `pool_size=10, max_overflow=20`(총 30
커넥션, `backend/database.py`)인 상황에서 요청 하나가 커넥션을 점유하는 시간이 늘어나자,
동시 요청이 몰릴 때 커넥션을 기다리다 타임아웃하는 요청이 발생하기 시작한 것입니다. 여기에
단일 uvicorn 워커(CPU 1코어 포화)까지 겹쳐 있어, 보안 수정 하나가 처리량 병목의 트리거가
됐습니다.

**해결책([Issue #4](https://github.com/Baguette-boost/backend-server/issues/4)) — 1차 시도는 틀렸고, 그 실패가 진짜 원인을 알려줬습니다**

1순위로 꼽았던 "DB 커넥션 풀 상향"(`pool_size 10→70, max_overflow 20→30`)을 먼저 실행하고
재측정했더니, 오히려 처리량이 **52→29 RPS**로 더 떨어지고 `QueuePool timed out`도 재발했습니다.
모니터링을 보니 DB CPU는 그때도 여유(12~17%)였고 **backend CPU만 그대로 1코어 포화**
상태였습니다 — 즉 병목은 처음부터 DB가 아니라 CPU였고, 원래의 작은 풀은 초과 요청을 빨리
실패시키는 일종의 백프레셔였는데 그걸 치우자 순수하게 손해였던 것입니다. 풀 크기는 원복하고,
진짜 원인인 **단일 uvicorn 워커**로 방향을 바꿨습니다.

`gunicorn -k uvicorn.workers.UvicornWorker -w 4`로 전환하되, 그 전에 멀티워커에서 깨지는
세 가지 프로세스-로컬 상태를 먼저 없애야 했습니다:
- GPS 인메모리 버퍼(`core/buffer.py`) → **Redis 리스트**로 전환(워커마다 버퍼가 조각나 배회
  판정이 깨지는 문제)
- 스케줄러(하트비트/낙상판정/배회enroll) → 워커와 무관하게 항상 1개만 도는 **별도 프로세스**
  (`baguetteboost-scheduler`)로 분리(워커 수만큼 중복 실행되어 알림이 N중 발송되는 문제)
- WebSocket 알림 → **Redis Pub/Sub 팬아웃**으로 전환(소켓이 붙은 워커와 알림이 발생한 워커가
  달라 대시보드 실시간 갱신이 조용히 누락되는 문제 — Expo 푸시는 DB 조회 기반이라 원래도
  안전했지만, 대시보드 즉시성은 워커 확률에 좌우되고 있었습니다)

**결과**: 처리량 52→**75 RPS**(평균, 램프 중 피크 ~90 RPS), 400명 p95 15s→**5.1s**,
`QueuePool` 타임아웃 29건→**0건**. 다만 SLA(p95<200ms) 기준 권장 용량은 ~90명으로
**개선되지 않았고**, 실패율도 0.27%→1.81%로 소폭 늘었습니다(단 원인은 DB 타임아웃이 아니라
`RemoteDisconnected` — 같은 4코어 호스트에서 부하생성기와 서버가 경쟁하는 테스트 환경
특성으로 추정, 확정은 아님). 남은 항목(배회 detect를 GPS 핑 경로에서 분리, 폴링→WebSocket
전환)은 계속 진행 예정입니다.

**성과 / 배운 점**
- "보안 수정은 공짜가 아니다" — 인증 강화가 가장 트래픽이 큰 경로에 DB 조회를 추가하는
  변경이라면, 그 대가를 수치로 미리 확인해야 한다는 걸 재확인했습니다.
- **"그럴듯한 1순위"가 틀릴 수 있다** — 이슈에 "즉효·저위험"으로 적어뒀던 DB 풀 상향이
  실제로는 역효과였습니다. 모니터링 지표(CPU가 DB가 아니라 backend에서 포화)가 없었다면
  틀린 방향으로 더 밀어붙였을 것입니다 — 추측 대신 리소스별 지표로 병목을 특정하는 습관이
  중요하다는 걸 실측으로 재확인했습니다.
- 부하테스트를 "한 번 재고 끝"이 아니라 **회귀 감지용으로 반복 실행**해야 의미가 있다는 것도
  이번에 체감했습니다. 최신 수치와 SVG 시각화(3회 측정 비교)는
  [`loadtest/report.html`](loadtest/report.html)에 있습니다.

---

## 한계와 다음 단계

- **[Issue #1](https://github.com/Baguette-boost/backend-server/issues/1)** — 배회는
  "환자별 개인화 모델"만 있고 범용(General) 모델이 없어 등록 전(보통 2주) 구간은 무방비,
  낙상은 반대로 "전역 모델"만 있고 환자별 파인튜닝이 없습니다. 이 갭은 AI 담당자의 모델
  학습 작업이 선행돼야 해서, 백엔드만으로는 해결할 수 없는 부분까지 조사해 이슈에 기록해
  두었고 착수는 AI 담당자 협의 이후로 보류 중입니다.
- **[Issue #4](https://github.com/Baguette-boost/backend-server/issues/4)** — DB 풀 상향
  시도(원복)와 uvicorn 멀티워커+Redis 전환은 완료([트러블슈팅 3](#3-부하테스트로-드러난-db-커넥션-풀-고갈)
  참고). 남은 항목: 배회 detect를 GPS 핑 경로에서 분리, 보호자 앱 폴링을 WebSocket 푸시로 전환.
- 공통 예외 응답 포맷이 아직 라우터별 `HTTPException` 직접 반환 방식이라 통일돼 있지 않습니다.
- 마이그레이션 시스템이 없어(`CREATE TABLE IF NOT EXISTS` 기반) 스키마 변경 시 이미 떠 있는
  운영 DB에는 수동 반영이 필요합니다.
