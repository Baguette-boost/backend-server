# SafeTrack API 문서

> 보호자 위치/상태 추적 앱 **SafeTrack**의 백엔드 API 레퍼런스.

---

## 목차
1. [공통 규약](#공통-규약)
2. [인증 (Auth)](#1-인증-auth)
3. [보호자 계정 (Guardian)](#2-보호자-계정-guardian)
4. [추적 대상 (Persons)](#3-추적-대상-persons)
5. [위치 데이터 (Telemetry)](#4-위치-데이터-telemetry)
6. [실시간 (WebSocket)](#4-1-실시간-websocket)
7. [안전구역 (Geofence)](#5-안전구역-geofence)
8. [알림 (Alerts)](#6-알림-alerts)
9. [푸시 알림 (Push)](#7-푸시-알림-push)
10. [대시보드 요약 (Summary)](#8-대시보드-요약-summary)
11. [통화 (Call)](#9-통화-call)
12. [디바이스 데이터 수집 (HW Telemetry)](#10-디바이스-데이터-수집-HW-Telemetry)
13. [Pytorch와 FastAPI 간 내부 API (Internal API)](#11-Pytorch와-FastAPI-간-내부-API-Internal-API)
14. [데이터 모델](#데이터-모델)

---

## 공통 규약

| 항목 | 값 |
|---|---|
| Base URL | `https://api.safetrack.example/v1` (env `EXPO_PUBLIC_API_URL`) |
| 인증 헤더(보호자 앱) | `Authorization: Bearer <accessToken>` |
| HW 디바이스 | `Authorization: Device <deviceToken>` |
| Content-Type | `application/json` |
| 시간 형식 | ISO 8601, KST 오프셋 (`2026-06-19T09:41:00+09:00`) |
| 문자 인코딩 | UTF-8 |

### 인증 흐름
1. `POST /auth/login` → `accessToken` + `refreshToken` 수령.
2. 모든 요청에 `Authorization: Bearer <accessToken>`.
3. `401` 수신 시 `POST /auth/refresh`로 갱신 후 재요청. 갱신 실패 시 로그아웃.

### 에러 응답
모든 4xx/5xx는 동일 포맷:
```json
{ "error": { "code": "person_not_found", "message": "대상을 찾을 수 없습니다." } }
```

| 상태 | 의미 |
|---|---|
| `400` | 잘못된 요청(검증 실패) |
| `401` | 미인증/토큰 만료 |
| `403` | 권한 없음 |
| `404` | 리소스 없음 |
| `409` | 충돌(중복 등록 등) |
| `500` | 서버 오류 |

### 페이지네이션
목록 API(알림/히스토리)는 커서 기반:
- 요청: `?cursor=<opaque>&limit=20`
- 응답: `{ "items": [{...}, ...], "nextCursor": "<opaque|null>" }`

---

## 1. 인증 (Auth)

### `POST /auth/signup`
보호자 회원가입

요청
```json
{ "name": "김보호자", "phone": "01012345678", "password": "••••••••" }
```
응답 `201`
```json
{
  "message": "회원가입이 완료되었습니다."
}
```

### `POST /auth/login`
보호자 로그인. 홈 헤더의 사용자 이름은 응답의 `guardian.name`에서 주입.

요청
```json
{ "phone": "01012345678", "password": "••••••••" }
```
응답 `200`
```json
{
  "accessToken": "eyJhbGci...",
  "refreshToken": "eyJhbGci...",
  "guardian": { "id": "g1", "name": "김보호자", "phone": "01012345678" }
}
```

### `POST /auth/refresh`
요청(헤더) `{ "refreshToken": "eyJhbGci..." }` → 응답(헤더) `{ "accessToken": "eyJhbGci..." }`

### `POST /auth/logout`
현재 토큰 무효화. 응답 `204`. (내정보 §4.3 로그아웃)

---

## 2. 보호자 계정 (Guardian)

### `GET /me`
응답 `200`
```json
{ "id": "g1", "name": "김보호자", "phone": "01012345678" }
```

### `PATCH /me`
요청(부분) `{ "name": "김보호자", "phone": "01099998888" }` → 갱신된 `Guardian`.

### `GET /me/settings` · `PATCH /me/settings`
알림 토글 등(내정보 §4.3 "알림 설정").
```json
{ "pushEnabled": true, "zoneExitAlert": true }
```

---

## 3. 추적 대상 (Persons)
홈 가족 카드 리스트 · 지도 마커 · 내정보 "추적 대상 관리"의 데이터 소스(대상).

### `GET /persons`
응답 `200` — `TrackedPerson[]`
```json
[
  {
    "id": "1", "name": "김순자", "age": 78, "avatarInitial": "김", "status": "alert", 
    "location": { "address": "역삼로 24", "zoneLabel": "안전구역 이탈", "inSafeZone": false, "isFallConfirmed": true, "lat": 37.501, "lng": 127.036, "updatedAt": "2026-06-19T09:41:00+09:00" }
  }, {}, ...
]
```

### `GET /persons/:id`
단일 대상 상세(`TrackedPerson`).

### `POST /persons/verify`
기기 검증.

요청
```json
{ "deviceId": "TRK-9F2C-1180" }
```
응답 `200`
```json
{ "message": "등록 가능한 기기입니다." }
```

### `POST /persons`
대상 등록(기기 페어링).

요청
```json
{ "name": "김순자", "age": 78, "deviceId": "TRK-9F2C-1180" }
```
응답 `201`
```json
{
  "personId": "1",
  "name": "김순자",
  "deviceToken": "st_live_secret_abc123...", // 디바이스가 통신 시 사용할 인증키
  "createdAt": "2026-06-19T09:41:00+09:00"
}
```

### `PATCH /persons/:id`
요청(부분) `{ "name": "...", "age": 9 }` → 갱신된 `TrackedPerson`.

### `DELETE /persons/:id`
응답 `204`.

---

## 4. 위치 데이터 (Telemetry)
홈 카드의 지도 좌표. 실시간성이 핵심 → WebSocket 권장, 폴링 폴백.

### `GET /persons/:id/location`
```json
{
  "address": "역삼로 24", "zoneLabel": "안전구역 이탈", "inSafeZone": false,
  "latitude": 37.501, "longitude": 127.036, "isFallConfirmed": true, "updatedAt": "2026-06-19T09:41:00+09:00"
}
```

### `GET /persons/:id/history?from=2026-06-19T09:25:00&to=2026-06-19T09:35:00`
위치 이동 경로(지도 라인).
```json
[ { "latitude": 37.500, "longitude": 127.034, "updatedAt": "2026-06-19T09:30:00+09:00" }, ... ]
```

---

## 4-1. 실시간 (WebSocket)
폴링 대신 실시간 갱신. 연결: `wss://api.safetrack.example/realtime`
(커스텀헤더에 accessToken 실어보냄)

서버 → 클라이언트 이벤트(JSON):
```json
{ "type": "location",  "personId": "1", "data": { "lat": 37.502, "lng": 127.037, "address": "...", "zoneLabel": "...", "inSafeZone": false, "isFallConfirmed": true, "updatedAt": "..." } }
{ "type": "status",    "personId": "1", "status": "alert" }
{ "type": "alert",     "alert": { "id": "a9", "personId": "1", "type": "zone_exit", "message": "...", "createdAt": "...", "read": false } }
```
> 미지원 환경은 `GET /persons/:id/location` 폴링(15~30초)으로 폴백.

---

## 5. 안전구역 (Geofence)
지도의 안전구역 조회, "안전구역 이탈" 판정, 내정보 "안전구역 설정".

### `GET /persons/:id/zones`
안전구역 조회

응답 `200`
```json
[
  { "id": "z1", "personId": "2", "label": "주간보호센터", "shape": "circle",
    "center": { "lat": 37.503, "lng": 127.044 }, "radius": 150 }, ...
]
```

### `POST /persons/:id/zones`
안전구역 설정

요청
```json
{ "label": "집", "shape": "circle", "center": { "lat": 37.50, "lng": 127.03 }, "radius": 100 }
```
응답 `201` — 생성된 `SafeZone`. (`polygon`은 `points: [{lat,lng}, ...]`)

### `PATCH /persons/:id/zones/:zoneId` · `DELETE /persons/:id/zones/:zoneId`
안전구역 수정 / 삭제(`204`).

---

## 6. 알림 (Alerts)
알림 탭 리스트, 홈 경보 배너/벨 점, 탭바 미확인 배지.

### `GET /alerts?filter=all|unread&personId=&cursor=&limit=`
알림 리스트를 시간 역순으로 조회.

응답 `200`
```json
{
  "items": [
    { "id": "a1", "personId": "1", "alertType": "zone_exit", "message": "안전구역을 이탈했습니다",
      "createdAt": "2026-06-19T09:41:00+09:00", "read": false }
  ],
  "nextCursor": null
}
```
> `type`: `zone_exit` · `fall_detected` · `offline`

### `GET /alerts/unread-count`
```json
{ "count": 3 }
```
> 탭바 미확인 배지 · 홈 벨 빨간 점에 사용.

### `PATCH /alerts/:id/read`
단건 읽음 처리 → 갱신된 `AlertItem`.

### `POST /alerts/read-all`
전체 읽음 처리. 응답 `204`.

---

## 7. 푸시 알림 (Push)
경보 발생 시 보호자 단말 푸시. Expo Push Token 등록.

### `POST /devices/push-token`
```json
{ "token": "ExponentPushToken[xxxxxxxx]", "platform": "ios" }
```
응답 `204`.

### `DELETE /devices/push-token`
로그아웃 시 토큰 해제. 응답 `204`.

---

## 8. 대시보드 요약 (Summary)
홈 요약 카드(총 추적 인원/안전/활성 경보). 대상이 적으면 클라이언트 파생도 가능.

### `GET /dashboard/summary`
```json
{ "totalCount": 4, "safeCount": 1, "alertCount": 2 }
```

---

## 9. 통화 (Call)
홈 가족 카드 "전화" 버튼은 네이티브 `tel:`(`Linking.openURL`)로 처리 — **서버 API 불필요**.
필요 시 통화 시도 기록만 선택적으로:

### `POST /persons/:id/call-log`
응답 `204`. (선택 기능)

---

## 10. 디바이스 데이터 수집 (HW Telemetry)

### `POST /telemetry/gps`
ESP32 기기가 10초 주기로 위경도 전송

요청
```json
{
  "personId": "1", // 기기가 환자 고유 ID 인지하고 있
  "latitude": 37.50123,
  "longitude": 127.03645,
  "timestamp": "2026-06-19T09:41:00+09:00"
}
```
응답 `200`
```json
{
  "status": "buffered"
}
```

### `POST /telemetry/fall-suspect`
ESP32 기기에서 가속도 임계치 초과 시, 직전 10초 ~ 직후 2초(총 12초) 분량의 raw 데이터 전송(gps 데이터 추가해서 POST /predict로 포워딩)

요청
```json
{
  "personId": "1",
  "timestamp": "2026-06-19T09:41:05+09:00",
  "imuData": {
    "ax": [0.01, 0.05, ...], 
    "ay": [0.98, 1.02, ...],
    "az": [-0.15, -0.10, ...],
    "wx": [0.0, 0.1, ...],
    "wy": [0.0, 0.0, ...],
    "wz": [0.5, 0.4, ...]
    "...(총 12초 분량의 시계열 고주파 데이터 배열)..."
  }
}
```
응답 `202`
```json
{ "status": "processing" }
```

## 11. Pytorch와 FastAPI 간 내부 API (Internal API)

### `POST /predict`
웹 서버 컨테이너가 디바이스로부터 받은 gps(10초 주기)와 imu(자체의심 시 이전 10초 + 2초 기다림 = 총 12초 분량) 시계열 데이터를 AI 컨테이너(http://ai-service:8000)에 던져주는 규약

요청
```json
{
  "personId": 1042,
  "timestamp": "2026-06-30T19:49:33Z",
  "imuData": {
    /* ex. 50hz * 12초 = 각 배열당 최대 600개의 float 데이터가 순서대로 적재됨. 받은 게 없다면 null */
    "ax": [0.01, 0.05, ...], 
    "ay": [0.98, 1.02, ...],
    "az": [-0.15, -0.10, ...],
    "wx": [0.0, 0.1, ...],
    "wy": [0.0, 0.0, ...],
    "wz": [0.5, 0.4, ...]
  },
  "gpsData": [
    /* 10초 주기, 30분 치 = 최대 약 180개의 GPS 객체 배열 */
    {
      "timestamp": "2026-06-30T19:19:33Z",
      "latitude": 36.6372,
      "longitude": 127.4897
    },
    ...
  ]
}
```

응답 `200`
```json
{
  "personId": 1042,
  "fall_detection": { // null 가능
    "is_triggered": true,
    "probability": 0.945
  },
  "wandering_detection": { // null 가능
    "is_triggered": false,
    "probability": 0.12
  }
}
```

## 데이터 모델

소스: `apps/safetrack/src/types.ts`

```ts
type SafetyStatus = 'safe' | 'alert' | 'offline';
type AlertType = 'zone_exit' | 'fall_detected' | 'offline';

interface TrackedPerson {
  id: string;
  name: string;
  age: number;
  avatarInitial: string;
  status: SafetyStatus;
  isFallConfirmed: boolean;
  location: { address: string; zoneLabel: string; inSafeZone: boolean; lat: number; lng: number };
  lastUpdated: string; // ISO
}

interface Guardian { id: string; name: string; phone: string }

interface AlertItem {
  id: string;
  personId: string;
  type: AlertType;
  message: string;
  createdAt: string;   // ISO
  read: boolean;
}

interface SafeZone {
  id: string;
  personId: string;
  label: string;
  shape: 'circle' | 'polygon';
  center?: { lat: number; lng: number };
  radius?: number;                       // m, circle
  points?: { lat: number; lng: number }[]; // polygon
}
```

---

## 엔드포인트 ↔ 클라이언트 매핑

| 클라이언트 호출 | 엔드포인트 |
|---|---|
| `api.auth.login(body)` | `POST /auth/login` |
| `api.guardian.me()` | `GET /me` |
| `api.persons.list()` | `GET /persons` |
| `api.persons.get(id)` | `GET /persons/:id` |
| `api.telemetry.location(id)` | `GET /persons/:id/location` |
| `api.telemetry.metrics(id)` | `GET /persons/:id/telemetry` |
| `api.zones.list(id)` | `GET /persons/:id/zones` |
| `api.alerts.list({filter})` | `GET /alerts` |
| `api.alerts.markRead(id)` | `PATCH /alerts/:id/read` |
| `api.push.register(body)` | `POST /devices/push-token` |
| `api.dashboard.summary()` | `GET /dashboard/summary` |
| `HW.telemetry.sendGps(body)` | `POST /telemetry/gps` |
| `HW.telemetry.sendFallSuspect(body)` | `POST /telemetry/fall-suspect` |
| `api.alerts.unreadCount()` | `GET /alerts/unread-count` |
| `api.alerts.readAll()` | `POST /alerts/read-all` |
