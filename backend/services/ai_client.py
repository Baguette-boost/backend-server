import httpx
import logging
from backend.config import settings
from backend.schemas.ai import AIPredictRequest, AIPredictResponse, DetectionResult

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self):
        self.client: httpx.AsyncClient | None = None

    def start(self):
        self.client = httpx.AsyncClient(
            base_url=settings.AI_SERVICE_URL,
            timeout=settings.AI_REQUEST_TIMEOUT
        )

    async def stop(self):
        if self.client:
            await self.client.aclose()

    async def predict(self, payload: AIPredictRequest) -> AIPredictResponse | None:
        if not self.client:
            logger.error("AI Client is not initialized.")
            return None

        try:
            # Pydantic 모델을 JSON 직렬화하여 전송
            response = await self.client.post("/predict", json=payload.model_dump(mode='json'))
            response.raise_for_status()
            
            return AIPredictResponse(**response.json())

        except httpx.TimeoutException:
            logger.error(f"AI Service timeout for personId: {payload.personId}")
        except httpx.RequestError as e:
            logger.error(f"Connection error to AI Service: {e}")

            # AI 컨테이너 미연결 시 임시 mock: 비트리거(감지 없음) 반환.
            # TODO: AI 컨테이너 연동 후 이 mock 블록 제거.
            return AIPredictResponse(
                personId=payload.personId,
                fall_detection=DetectionResult(is_triggered=False, probability=0.0),
                wandering_detection=DetectionResult(is_triggered=False, probability=0.0),
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"AI Service returned HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"Unexpected error during AI prediction: {e}")
            
        return None

# 전역 인스턴스 생성
ai_client = AIClient()
