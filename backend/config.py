from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    DEBUG_MODE: bool
    # 낙상/배회 추론 컨테이너 (Stage 3: 역할별 분리)
    AI_FALL_URL: str = "http://baguetteboost-ai-fall:5000"
    AI_WANDER_URL: str = "http://baguetteboost-ai-wander:8000"
    AI_REQUEST_TIMEOUT: int = 5 # 타임아웃 5초 설정
    # 멀티워커 간 공유 상태(GPS 버퍼, WebSocket Pub/Sub 팬아웃)용
    REDIS_URL: str = "redis://baguetteboost-redis:6379/0"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # Production JWT access token 만료(분)
    MYSQL_ROOT_PASSWORD: str
    MYSQL_DATABASE: str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    TZ: str
    MY_EXPO_TOKEN: str

    model_config = SettingsConfigDict(
        env_file = ".env",
        # .env 파일이 없을 경우 시스템 환경변수를 우선적으로 찾도록 설정
        env_file_encoding = 'utf-8'
    )

settings = Settings()
