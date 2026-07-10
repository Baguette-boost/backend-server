from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    DEBUG_MODE: bool
    # 낙상/배회 추론 컨테이너 (Stage 3: 역할별 분리)
    AI_FALL_URL: str = "http://baguetteboost-ai-fall:5000"
    AI_WANDER_URL: str = "http://baguetteboost-ai-wander:5000"
    AI_REQUEST_TIMEOUT: int = 5 # 타임아웃 5초 설정
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
