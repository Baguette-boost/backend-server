# Voost 백엔드 API 문서

> 이 문서는 `backend/routers/`·`backend/schemas/`의 **실제 구현**을 기준으로 작성되었습니다.
> 이전 버전은 초기 기획 단계에 다른 프로젝트명(`SafeTrack`)으로 작성된 문서였고 구현과
> 여러 곳에서 어긋나 있어 [`API.prev.md`](API.prev.md)로 이름을 바꿔 보관합니다(참고용,
> 신뢰하지 말 것).

## 목차
1. [공통 규약](#공통-규약)
2. [인증 (Auth)](#1-인증-auth)
3. [보호자 계정 (Guardian)](#2-보호자-계정-guardian)
4. [환자 관리 (Persons)](#3-환자-관리-persons)
5. [디바이스 데이터 수집 (HW Telemetry)](#4-디바이스-데이터-수집-hw-telemetry)
6. [실시간 (WebSocket)](#5-실시간-websocket)
7. [알림 (Alerts)](#6-알림-alerts)
8. [푸시 알림 (Push)](#7-푸시-알림-push)
9. [대시보드 요약](#8-대시보드-요약)
10. [내부 API (Backend ↔ AI 컨테이너)](#9-내부-api-backend--ai-컨테이너)
11. [데이터 모델](#데이터-모델)
12. [전체 엔드포인트 목록](#전체-엔드포인트-목록)

---

## 공통 규약

| 항목 | 값 |
|---|---|
| 인증 헤더(보호자 앱) | `Authorization: Bearer <access_token>` |
| 인증 헤더(HW 디바이스) | `Authorization: Device <device_token>` |
| Content-Type | `application/json` |
| 시간 형식 | ISO 8601 UTC(`...Z`). 서버는 오프셋이 붙어 와도 방어적으로 UTC로 정규화하지만, 보내는 쪽은 UTC로 통일할 것 |
| 문자 인코딩 | UTF-8 |

### 인증 흐름
1. `POST /auth/login` → `access_token` + `refresh_token` 수령.
2. 모든 요청에 `Authorization: Bearer <access_token>`.
3. `401` 수신 시 헤더 `refresh-token`으로 `POST /auth/refresh` 호출 후 재요청. 갱신 실패 시 로그아웃.

### 에러 응답
공통 에러 포맷이 아직 없습니다(백로그). 라우터별로 `HTTPException`을 직접 반환하며 실제로는
아래 두 형태가 섞여 있습니다.

```jsonc
// 1) 라우터가 명시적으로 던진 HTTPException — 대부분의 4xx
{ "detail": "Invalid Device Token" }
```
```jsonc
// 2) FastAPI/Pydantic 자체 검증 실패(예: IMU 9채널 길이 불일치, 필수 필드 누락) — 422
{ "detail": [ { "type": "value_error", "loc": ["body", "imuData"], "msg": "...", "input": {...} } ] }
```

| 상태 | 의미 |
|---|---|
| `400` | 잘못된 요청(예: `/persons/verify`의 유효하지 않은 device_token) |
| `401` | 미인증/토큰 만료/잘못된 토큰 |
| `403` | 권한 없음(다른 보호자의 환자 접근, device_token과 personId 불일치) |
| `404` | 리소스 없음 |
| `409` | 충돌(device_token 중복 등록 등) |
| `422` | Pydantic 스키마 검증 실패 |
| `500` | 서버 오류 |

### 페이지네이션
**커서 기반 페이지네이션은 구현돼 있지 않습니다.** `GET /alerts`는 `limit`(최신순 상한)만
지원하고, `GET /persons/{id}/history`는 `limit`(초과 시 최신순으로 잘라 오래된 점부터 버림)만
지원합니다. `nextCursor`, `filter=unread` 같은 파라미터는 없습니다.

---

## 1. 인증 (Auth)
`backend/routers/auth.py`, 스키마: `backend/schemas/guardian.py`

### `POST /auth/signup`
보호자 회원가입.

```jsonc
// 요청
{ "name": "김보호자", "phone": "01012345678", "password": "12345678", "expo_token": "ExponentPushToken[xxxx]" }
```
- `password`는 최소 8자(스키마 `min_length=8`).
- `expo_token`은 **필수 필드**입니다 — 앱은 회원가입 요청 전에 반드시 Expo 푸시 토큰을 먼저
  발급받아야 합니다(선택적 null 아님).

```jsonc
// → 201 (로그인과 동일한 TokenResponse)
{ "access_token": "eyJhbGciOi...", "refresh_token": "n3F9x...", "token_type": "Bearer" }
```

### `POST /auth/login`
```jsonc
// 요청
{ "phone": "01012345678", "password": "12345678" }
```
- `password`도 최소 8자 검증이 걸려 있어, 8자 미만이면 자격 확인 이전에 `422`가 납니다.

```jsonc
// → 200
{ "access_token": "eyJhbGciOi...", "refresh_token": "n3F9x...", "token_type": "Bearer" }
```
- `DEBUG_MODE=true`(기본값)일 때는 `access_token`/`refresh_token`이 각각
  `"{guardian_id}_ACCESS"` / `"{guardian_id}_REFRESH"` 형태의 더미 문자열입니다.
- `DEBUG_MODE=false`(운영)일 때는 `access_token`이 HS256 서명 JWT(`sub=phone`, 만료 60분),
  `refresh_token`은 서명이 아닌 불투명 랜덤 문자열(`secrets.token_urlsafe(32)`)입니다.

### `POST /auth/refresh`
```
헤더: refresh-token: <refresh_token>
```
> FastAPI가 파라미터명 `refresh_token`을 케밥케이스 헤더명(`refresh-token`)으로 자동
> 변환해서 찾습니다. `refreshToken` 같은 camelCase 헤더로 보내면 인식되지 않습니다.

응답은 `POST /auth/login`과 동일한 `TokenResponse`(재발급된 새 토큰 쌍, 둘 다 회전됨).

### `POST /auth/logout`
`Authorization: Bearer` 필요. 응답 `204`(본문 없음). `access_token`/`refresh_token`을
DB에서 빈 문자열로 무효화합니다. **`expo_token`은 지워지지 않습니다** — [§7](#7-푸시-알림-push) 참고.

---

## 2. 보호자 계정 (Guardian)
`backend/routers/guardians.py`

### `GET /me`
```jsonc
// → 200
{ "id": 1, "name": "김보호자", "phone": "01012345678" }
```

### `PATCH /me`
```jsonc
// 요청(부분) — name/phone만 수정 가능
{ "name": "김보호자2" }
// → 200: 요청 바디를 그대로 반환(주의: id 등 나머지 프로필 필드는 응답에 없음)
```

### `GET /me/settings` · `PATCH /me/settings`
```jsonc
// → 200
{ "user_id": 1, "push_enabled": true }
```
- 요청 바디로 갱신 가능한 필드는 `push_enabled` 하나뿐입니다(`zone_exit_alert` 같은 값은
  스키마·테이블 어디에도 없습니다).

---

## 3. 환자 관리 (Persons)
`backend/routers/persons.py`, 스키마: `backend/schemas/person.py`

### `POST /persons`
환자 등록 및 디바이스 페어링. **디바이스가 이미 가진 `device_token`을 클라이언트가 보내고,
서버가 `device_id`(UUID)를 새로 생성**합니다(방향에 유의 — 기기 쪽이 토큰을 먼저 가지고
있다고 가정한 설계입니다).

```jsonc
// 요청 (Authorization: Bearer <access_token>)
{
  "name": "김순자", "age": 78,
  "device_token": "AA:BB:CC:DD:EE:FF",   // 스키마 설명상 "페어링할 기기의 MAC 주소"
  "phone_number": "01099998888"          // 선택
}
```
```jsonc
// → 201
{
  "id": 1, "name": "김순자", "age": 78,
  "deviceId": "8f2e2b1a-9c3d-4e2a-9f1a-...",   // 서버 생성 UUID
  "deviceToken": "AA:BB:CC:DD:EE:FF",
  "phoneNumber": "01099998888",
  "wanderingEnrolled": false,
  "createdAt": "2026-08-22T00:41:00Z"
}
// device_token 중복 등록 시 409
```
> 응답에 `is_active`/`is_fall`/`is_wandering` 등 **현재 상태 필드가 없습니다.** 상태 확인은
> `GET /persons/{id}/location`을 별도 호출해야 합니다 — [§8](#8-대시보드-요약) 참고.

### `GET /persons`
```jsonc
// → 200: PersonResponse 배열(위 응답과 동일한 형태)
[ { "id": 1, "name": "김순자", ... }, ... ]
// 내가 관리하는 환자가 0명이면 404
```

### `GET /persons/{id}` · `PATCH /persons/{id}` · `DELETE /persons/{id}`
소유권 검증(내 환자인지) 후 조회/부분수정(`name`/`age`/`phone_number`)/삭제(`204`).

### `POST /persons/verify`
디바이스가 페어링 이전에 자기 토큰이 유효한지 스스로 확인할 때 사용(인증 불필요, 조회만).
```jsonc
// 요청
{ "device_token": "AA:BB:CC:DD:EE:FF" }
// → 200
{ "status": "verified", "mapped_person_id": 1 }
// 등록되지 않은 토큰이면 400 { "detail": "Invalid Device Token" }
```

### `GET /persons/{id}/location`
```jsonc
// → 200
{ "latitude": 37.501, "longitude": 127.036, "is_fall": false, "is_wandering": true }
// 위치 로그가 한 건도 없으면 404
```
> 주소(역지오코딩), 최근 갱신 시각 필드는 없습니다 — 필요하면 좌표만으로 클라이언트가 처리해야 합니다.

### `GET /persons/{id}/history?from=&to=&limit=`
`from`/`to`는 필수 쿼리 파라미터(ISO 8601), `limit` 기본 5000(최대 20000).
```jsonc
// → 200
{
  "history": [
    { "latitude": 37.500, "longitude": 127.034, "is_fall_detected": false, "is_wandering_detected": false },
    ...
  ]
}
```
> 배열이 아니라 `{"history": [...]}`로 감싸져 있고, 포인트별 시간 필드가 없습니다(구간
> 자체가 `from`/`to`이므로 개별 타임스탬프까지는 내려주지 않는 설계).

### `PATCH /persons/{id}/status`
대시보드에서 낙상/배회 상태를 수동으로 정상화(해제)할 때 사용.
```jsonc
// 요청 — is_fall/is_wandering 중 하나 이상
{ "is_fall": false }
// → 200
{ "id": 1, "is_fall": false, "is_wandering": true }
```
`is_fall: false`를 보내면 낙상 에피소드 자체가 종료 처리되어(스케줄러가 되돌리지 않음) 해당
환자의 미확인 `fall_detected` 알림도 함께 읽음 처리됩니다(배회도 동일 패턴). 성공 시 같은
보호자의 WebSocket 연결로 `status_update` 이벤트가 즉시 발행됩니다([§5](#5-실시간-websocket)).

### `POST /persons/{id}/wandering/re-enroll`
배회 개인모델을 최근 14일 GPS로 즉시 재학습(생활패턴이 정당하게 바뀐 경우에만 사용 — 자동
주기 재등록은 없음, 이 수동 트리거만 존재).
```jsonc
// → 200 (성공)
{ "personId": 1, "wanderingEnrolled": true, "enrolled": true, "fixes": 1234, "days": 9, "threshold": 0.42 }
// → 422 (게이트 미충족)
{ "detail": "Re-enrollment failed (insufficient_data): last 14 days fix=210, days=4 (gate not met)" }
```

---

## 4. 디바이스 데이터 수집 (HW Telemetry)
`backend/routers/telemetry.py`, 스키마: `backend/schemas/telemetry.py`. 인증: `Authorization: Device <device_token>`.

### `POST /telemetry/gps`
ESP32가 10초 주기로 위경도 전송. 배회 판정으로만 이어짐(낙상과 무관).
```jsonc
// 요청
{ "personId": 1, "gps": { "latitude": 37.50123, "longitude": 127.03645, "timestamp": "2026-08-22T00:41:00Z" } }
// → 201
{ "status": "success", "message": "Data received and AI analysis started" }
```

### `POST /telemetry/fall-suspect`
가속도 임계치 초과 시 직전 10초~직후 2초(총 12초) IMU 원본 전송. 낙상 판정으로만 이어짐.
```jsonc
// 요청
{
  "personId": 1,
  "timestamp": "2026-08-22T00:41:05Z",
  "imuData": { "roll": [...], "pitch": [...], "yaw": [...], "ax": [...], "ay": [...], "az": [...], "wx": [...], "wy": [...], "wz": [...] }
}
// → 202
{ "status": "accepted", "message": "Fall suspect data is being processed" }
```
- 9개 채널(`roll..wz`)은 길이가 모두 같아야 합니다(다르면 `422`).
- `device_token`의 주인(`personId`)과 요청 바디의 `personId`가 다르면 `403`
  (`Device token does not match personId.`).

---

## 5. 실시간 (WebSocket)
`backend/routers/realtime.py`

연결: `wss://<host>/realtime`, 커스텀 헤더 `accessToken: <access_token>`(REST와 동일한
토큰·검증 로직 공유). 토큰이 없거나 유효하지 않으면 코드 `1008`(Policy Violation)로 연결을
거부합니다. 한 보호자가 여러 기기/탭에서 동시 접속하면 모든 연결에 동일하게 브로드캐스트됩니다.

**서버 → 클라이언트**로 실제 발행되는 이벤트는 아래 2종뿐입니다(문서에만 있던 `location`/
`status` 타입은 현재 발행되지 않습니다 — GPS 위치 자체는 WS로 실시간 푸시되지 않고, REST
폴링(`GET /persons/{id}/location`)으로만 조회 가능합니다).

```jsonc
// 낙상/배회/오프라인 최초 감지 시 1회 (에피소드당 1회로 디바운스됨)
{
  "type": "alert",
  "alert": {
    "personId": "1",
    "type": "fall_detected",     // wandering | fall_detected | offline
    "message": "Fall detected for 김순자.",
    "createdAt": "2026-08-22T00:41:05Z",
    "read": false
  }
}
```
> ⚠️ `alert.id`(DB의 `alert_logs.id`)가 페이로드에 없습니다 — 클라이언트가 이 이벤트만으로는
> `PATCH /alerts/{id}/read`를 바로 호출할 수 없고, `GET /alerts`로 다시 조회해 id를 얻어야
> 합니다(알려진 갭, 백로그).

```jsonc
// PATCH /persons/{id}/status 로 수동 정상화될 때
{
  "type": "status_update",
  "status": { "personId": "1", "isFall": false, "isWandering": true, "updatedAt": "2026-08-22T00:41:10Z" }
}
```

클라이언트가 서버로 보내는 메시지는 현재 아무것도 처리되지 않습니다(연결 유지 목적의
`receive_text()` 호출만 있고, 그 내용을 소비하는 로직은 아직 없습니다).

미지원 환경(또는 재연결 중)은 `GET /persons/{id}/location` 폴링(15~30초)으로 폴백하세요.

---

## 6. 알림 (Alerts)
`backend/routers/alerts.py`, 스키마: `backend/schemas/alert.py`

### `GET /alerts?limit=50`
내가 관리하는 모든 환자의 알림을 시간 역순으로 조회. **`filter`/`personId`/커서 파라미터는
없습니다** — `limit`(기본 50, 최대 200)만 지원하며, 읽음/안읽음 필터링은 클라이언트가
직접 해야 합니다.
```jsonc
// → 200
[
  { "id": 12, "person_id": 1, "alertType": "wandering", "message": "Wandering detected for 김순자.",
    "is_read": false, "createdAt": "2026-08-22T00:41:00Z" }
]
```
> 필드명이 뒤섞여 있습니다(`id`/`person_id`/`is_read`는 snake_case, `alertType`/`createdAt`은
> camelCase) — 스키마의 `Field(alias=...)`가 일부 필드에만 붙어 있기 때문입니다. 응답은
> 배열을 바로 반환하며(`{items:[...]}`로 감싸지 않음), 페이지네이션 정보도 없습니다.

### `GET /alerts/unread-count`
```jsonc
// → 200
{ "unread_count": 3 }
```

### `PATCH /alerts/{id}/read`
```jsonc
// → 200
{ "message": "Alert 12 marked as read" }
```
> ⚠️ **소유권 검증이 없습니다** — `alert_id`가 내가 관리하는 환자의 것인지 확인하지 않고
> 바로 갱신합니다. 다른 보호자의 alert id를 알면 읽음 처리를 할 수 있는 상태입니다(백로그).

### `POST /alerts/read-all`
```jsonc
// → 200
{ "message": "All alerts marked as read" }
```
이쪽은 내가 관리하는 환자 id로 한정해서 갱신합니다(위 단건 처리와 달리 소유권이 실제로
적용되는 비대칭 상태).

---

## 7. 푸시 알림 (Push)

**별도의 푸시 토큰 등록/해제 엔드포인트는 없습니다.**
- `expo_token`은 `POST /auth/signup` 요청 바디에만 존재하는 **필수 필드**이며, 회원가입 시
  1회 저장된 뒤로는 갱신할 방법이 없습니다(`PATCH /me`도 `name`/`phone`만 받고
  `expo_token`은 다루지 않습니다).
- `POST /auth/logout`도 `expo_token`을 지우지 않습니다 — access/refresh 토큰만 무효화됩니다.
- 즉 앱을 재설치해 Expo 토큰이 바뀌거나, 토큰이 만료(`DeviceNotRegistered`)되는 경우 현재는
  **DB를 직접 갱신하는 것 외에 갱신 수단이 없습니다.** (`/devices/push-token` 같은 엔드포인트를
  추가하는 것이 백로그입니다.)

발송 자체는 `NotificationService._send_expo_push()`가 담당하며, 알림 발생(`wandering` /
`fall_detected` / `offline`) 시 WebSocket과 함께 Expo Server SDK로 전송됩니다. `expo_token`이
비어 있으면 조용히 스킵합니다(에러 없음, 로그만 남김).

---

## 8. 대시보드 요약

**`GET /dashboard/summary` 같은 집계 엔드포인트는 없습니다.** 홈 화면 요약(총 인원/안전/경보
수)이 필요하면 클라이언트가 아래처럼 직접 조합해야 합니다.

1. `GET /persons` → 전체 환자 목록(단, 이 응답엔 상태 필드가 없음).
2. 각 환자에 대해 `GET /persons/{id}/location` → `is_fall`/`is_wandering`으로 상태 판단
   (환자 수만큼 N+1 호출이 필요합니다 — 목록 하나로 상태까지 받는 API가 없습니다).
3. `GET /alerts/unread-count` → 벨 배지 숫자.

환자 수가 많아지면 이 N+1 패턴이 비용을 키우므로, 서버 쪽에 실제 요약/목록+상태 통합
엔드포인트를 추가하는 편이 좋습니다(현재 코드베이스에는 없음 — 백로그).

---

## 9. 내부 API (Backend ↔ AI 컨테이너)

### `POST /predict` (→ `baguetteboost-ai-fall`)
```jsonc
// 요청 (backend/schemas/ai.py: AIPredictRequest)
{
  "personId": 1042,
  "timestamp": "2026-06-30T19:49:33Z",
  "imuData": { "roll":[...], "pitch":[...], "yaw":[...], "ax":[...], "ay":[...], "az":[...], "wx":[...], "wy":[...], "wz":[...] } , // null 가능(GPS 전용 호출 시)
  "gpsData": [ { "timestamp": "2026-06-30T19:19:33Z", "latitude": 36.6372, "longitude": 127.4897 }, ... ]
}
// 응답
{
  "personId": 1042,
  "fall_detection": { "is_triggered": true, "probability": 0.945 },
  "wandering_detection": { "is_triggered": false, "probability": 0.12 }
}
```

### `POST /users/{personId}/detect` (→ `baguetteboost-ai-wander`, 팀원 원본)
```jsonc
// 요청
{ "fixes": [ { "lat": 36.6372, "lng": 127.4897 }, ... ] }   // 최근 최대 60점만 전송(지연 상한)
// 응답
{ "wandering": false, "max_prob": 0.12 }
// 미등록(학습 중) 환자면 404, 데이터 부족이면 422 — 둘 다 backend에서 "비트리거"로 흡수함
```

### `POST /users/{personId}/enroll` (→ `baguetteboost-ai-wander`)
최근 14일 GPS(20초 리샘플)로 환자 전용 RF 모델을 학습. 게이트: 유효 일수 ≥7 AND fix ≥1000.
하루 1회 스케줄러(새벽 3시)가 미등록 환자에 대해 자동 호출하거나, `POST /persons/{id}/wandering/re-enroll`로 수동 호출.

이 호출들은 항상 **DB 세션 밖에서**(`backend/services/ai_client.py`) 이루어집니다 — 추론
대기(최대 5초 타임아웃) 동안 DB 커넥션 풀을 점유하지 않기 위함입니다.

---

## 데이터 모델

아래는 실제 응답 스키마(`backend/schemas/`)를 기준으로 정리한 것입니다(가상의 클라이언트
타입 파일을 참조하지 않습니다 — 이 레포에는 프론트엔드 코드가 없습니다).

```ts
type AlertType = 'wandering' | 'fall_detected' | 'offline';

interface PersonResponse {
  id: number; name: string; age: number;
  deviceId: string; deviceToken: string;
  phoneNumber: string | null;
  wanderingEnrolled: boolean;
  createdAt: string;   // ISO 8601 UTC(...Z)
  // 주의: is_active/is_fall/is_wandering 없음 — 상태는 LocationResponse에서만 확인 가능
}

interface LocationResponse {
  latitude: number; longitude: number;
  is_fall: boolean; is_wandering: boolean;
}

interface LocationHistoryPoint {
  latitude: number; longitude: number;
  is_fall_detected: boolean; is_wandering_detected: boolean;
  // 개별 타임스탬프 없음
}

interface AlertResponse {
  id: number; person_id: number;
  alertType: AlertType; message: string; is_read: boolean;
  createdAt: string;   // ISO 8601 UTC(...Z)
}

interface Guardian { id: number; name: string; phone: string }
```

---

## 전체 엔드포인트 목록

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| POST | `/auth/signup` | - | 회원가입 |
| POST | `/auth/login` | - | 로그인 |
| POST | `/auth/refresh` | `refresh-token` 헤더 | 토큰 재발급 |
| POST | `/auth/logout` | Bearer | 토큰 무효화 |
| GET | `/me` | Bearer | 내 프로필 |
| PATCH | `/me` | Bearer | 프로필 수정 |
| GET | `/me/settings` | Bearer | 알림 설정 조회 |
| PATCH | `/me/settings` | Bearer | 알림 설정 수정 |
| POST | `/persons` | Bearer | 환자 등록 |
| GET | `/persons` | Bearer | 환자 목록 |
| GET | `/persons/{id}` | Bearer | 환자 상세 |
| PATCH | `/persons/{id}` | Bearer | 환자 정보 수정 |
| DELETE | `/persons/{id}` | Bearer | 환자 삭제 |
| POST | `/persons/verify` | - | 기기 토큰 검증 |
| GET | `/persons/{id}/location` | Bearer | 최신 위치·상태 |
| GET | `/persons/{id}/history` | Bearer | 위치 히스토리 |
| PATCH | `/persons/{id}/status` | Bearer | 상태 수동 정상화 |
| POST | `/persons/{id}/wandering/re-enroll` | Bearer | 배회 모델 수동 재학습 |
| POST | `/telemetry/gps` | Device | GPS 수신 |
| POST | `/telemetry/fall-suspect` | Device | 낙상 의심 IMU 수신 |
| GET | `/alerts` | Bearer | 알림 목록 |
| GET | `/alerts/unread-count` | Bearer | 미확인 수 |
| PATCH | `/alerts/{id}/read` | Bearer | 단건 읽음 처리 |
| POST | `/alerts/read-all` | Bearer | 전체 읽음 처리 |
| WS | `/realtime` | `accessToken` 헤더 | 실시간 이벤트 |
| GET | `/health` | - | 헬스체크 |
