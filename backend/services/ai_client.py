import httpx
import logging
from backend.config import settings
from backend.schemas.ai import AIPredictRequest, AIPredictResponse, DetectionResult

logger = logging.getLogger(__name__)


def _wander_result(person_id: int, triggered: bool, prob: float) -> AIPredictResponse:
    return AIPredictResponse(
        personId=person_id,
        fall_detection=None,
        wandering_detection=DetectionResult(is_triggered=triggered, probability=prob),
    )


class AIClient:
    def __init__(self):
        self.fall_client: httpx.AsyncClient | None = None
        self.wander_client: httpx.AsyncClient | None = None

    def start(self):
        # 낙상: 우리 predict_server(/predict) / 배회: 팀원 RF 서버(/users/{id}/detect)
        self.fall_client = httpx.AsyncClient(
            base_url=settings.AI_FALL_URL, timeout=settings.AI_REQUEST_TIMEOUT
        )
        self.wander_client = httpx.AsyncClient(
            base_url=settings.AI_WANDER_URL, timeout=settings.AI_REQUEST_TIMEOUT
        )

    async def stop(self):
        for c in (self.fall_client, self.wander_client):
            if c:
                await c.aclose()

    async def predict(self, payload: AIPredictRequest) -> AIPredictResponse | None:
        # IMU 가 있으면 낙상, 없으면(GPS만) 배회로 라우팅
        if payload.imuData is not None:
            return await self._predict_fall(payload)
        return await self._detect_wander(payload)

    # ── 낙상: /predict (AIPredictRequest → AIPredictResponse) ──
    async def _predict_fall(self, payload: AIPredictRequest) -> AIPredictResponse | None:
        if not self.fall_client:
            logger.error("AI fall client is not initialized.")
            return None
        try:
            response = await self.fall_client.post("/predict", json=payload.model_dump(mode="json"))
            response.raise_for_status()
            return AIPredictResponse(**response.json())
        except httpx.TimeoutException:
            logger.error(f"[FALL pid={payload.personId}] AI timeout")
        except httpx.RequestError as e:
            logger.error(f"[FALL pid={payload.personId}] 연결 오류: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"[FALL pid={payload.personId}] AI HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"[FALL pid={payload.personId}] 예외: {e}")
        return None

    # ── 배회: 팀원 RF 서버 POST /users/{personId}/detect {"fixes":[{lat,lng}]} ──
    async def _detect_wander(self, payload: AIPredictRequest) -> AIPredictResponse | None:
        if not self.wander_client:
            logger.error("AI wander client is not initialized.")
            return None
        person_id = payload.personId
        fixes = [
            {"lat": float(p.latitude), "lng": float(p.longitude)}
            for p in (payload.gpsData or [])
        ]
        # RF 는 최소 3점 필요(실질적으로 1윈도우=30점 이상이라야 판정). 부족하면 비트리거.
        if len(fixes) < 3:
            return _wander_result(person_id, False, 0.0)
        try:
            response = await self.wander_client.post(f"/users/{person_id}/detect", json={"fixes": fixes})
            if response.status_code == 404:
                # 미등록(학습 중) → 배회 비트리거 (조용히)
                logger.info(f"[WANDER pid={person_id}] 미등록(학습 중) → 비트리거")
                return _wander_result(person_id, False, 0.0)
            if response.status_code == 422:
                # 데이터 부족(1윈도우 미만 등) → 비트리거
                return _wander_result(person_id, False, 0.0)
            response.raise_for_status()
            data = response.json()
            # RF 의 디바운스된 최종 판정만 사용(wandering). null/false → False
            wandering = bool(data.get("wandering"))
            prob = float(data.get("max_prob") or 0.0)
            return _wander_result(person_id, wandering, prob)
        except httpx.TimeoutException:
            logger.error(f"[WANDER pid={person_id}] AI timeout")
        except httpx.RequestError as e:
            logger.error(f"[WANDER pid={person_id}] 연결 오류: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"[WANDER pid={person_id}] AI HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"[WANDER pid={person_id}] 예외: {e}")
        return None


# 전역 인스턴스 생성
ai_client = AIClient()
