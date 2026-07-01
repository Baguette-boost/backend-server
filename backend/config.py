from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str
    AI_SERVICE_URL: str
    AI_REQUEST_TIMEOUT: int = 5 # 타임아웃 5초 설정
    MYSQL_ROOT_PASSWORD: str
    MYSQL_DATABASE: str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    TZ: str

    class Config:
        env_file = ".env"
        # .env 파일이 없을 경우 시스템 환경변수를 우선적으로 찾도록 설정
        env_file_encoding = 'utf-8'

settings = Settings()
