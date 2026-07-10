import httpx
import logging
from backend.config import settings
from backend.schemas.ai import AIPredictRequest, AIPredictResponse

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self):
        self.fall_client: httpx.AsyncClient | None = None
        self.wander_client: httpx.AsyncClient | None = None

    def start(self):
        # Stage 3: 낙상/배회 추론 컨테이너 분리 → 역할별 클라이언트
        self.fall_client = httpx.AsyncClient(
            base_url=settings.AI_FALL_URL,
            timeout=settings.AI_REQUEST_TIMEOUT
        )
        self.wander_client = httpx.AsyncClient(
            base_url=settings.AI_WANDER_URL,
            timeout=settings.AI_REQUEST_TIMEOUT
        )

    async def stop(self):
        for c in (self.fall_client, self.wander_client):
            if c:
                await c.aclose()

    async def predict(self, payload: AIPredictRequest) -> AIPredictResponse | None:
        # 라우팅: IMU 가 있으면 낙상 컨테이너, 없으면(GPS만) 배회 컨테이너로 보낸다.
        client = self.fall_client if payload.imuData is not None else self.wander_client
        if not client:
            logger.error("AI Client is not initialized.")
            return None

        try:
            # Pydantic 모델을 JSON 직렬화하여 전송
            response = await client.post("/predict", json=payload.model_dump(mode='json'))
            response.raise_for_status()
            
            return AIPredictResponse(**response.json())

        except httpx.TimeoutException:
            logger.error(f"AI Service timeout for personId: {payload.personId}")
        except httpx.RequestError as e:
            logger.error(f"Connection error to AI Service: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"AI Service returned HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"Unexpected error during AI prediction: {e}")
            
        return None

# 전역 인스턴스 생성
ai_client = AIClient()
