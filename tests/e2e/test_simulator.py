from backend.database import get_db
from backend.services.notification_service import NotificationService

from backend.models.alert import AlertLog 

import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport

from backend.main import app

pytestmark = pytest.mark.asyncio # 파일 내 모든 async 테스트 자동 인식

@pytest_asyncio.fixture
async def async_client():
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client

async def test_trigger_fall(async_client):
    """
    [시뮬레이터] 가상 낙상 이벤트 트리거
    """
    person_id = "123"

    response = await async_client.post(f"/test/trigger/fall?person_id={person_id}")

    if response.status_code == 500:
        print(f"\n[서버 에러 상세 원인]: {response.json()}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

async def test_trigger_wandering(async_client):
    """
    [시뮬레이터] 가상 배회 이벤트 트리거
    """

    person_id = "123"

    response = await async_client.post(f"/test/trigger/wandering?person_id={person_id}")

    if response.status_code == 500:
        print(f"\n[서버 에러 상세 원인]: {response.json()}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"