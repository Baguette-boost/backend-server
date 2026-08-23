from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from backend.config import settings

# 비동기 MySQL 엔진 연결
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # SQL 로그 홍수 방지(필요 시 디버깅에서만 True). setup_logging 에서도 sqlalchemy.engine=WARNING
    pool_pre_ping=True,
    # Issue #4에서 pool_size/max_overflow만 올려(10+20→70+30) 재측정했더니 오히려 처리량이
    # 떨어지고 지연이 늘었다(RPS 52→29, p95 15s→21s) — DB는 병목이 아니라 backend(단일
    # uvicorn 워커) CPU가 이미 포화 상태였고, 풀만 키우면 이미 포화인 CPU를 더 많은 요청이
    # 기다리게 만들 뿐이었다. 원복하고 워커 확장(Issue #4 2단계)을 먼저 진행한다.
    pool_size=10,
    max_overflow=20,
    # 커넥션마다 세션 타임존을 UTC 로 고정한다.
    # 이렇게 하면 func.now()/CURRENT_TIMESTAMP 기본값이 DB 서버 설정과 무관하게 항상 UTC 로 기록된다.
    connect_args={"init_command": "SET time_zone = '+00:00'"}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

# DB 세션 의존성 주입용 함수
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback() # 에러 발생 시 안전하게 롤백
            raise
        finally:
            await session.close()

@asynccontextmanager
async def get_independent_session():
    """ HTTP 컨텍스트 외부(스케줄러, BackgroundTasks)에서 수동으로 세션을 제어할 때 사용 """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit() # 정상 종료 시 자동 커밋
    except Exception as e:
        await session.rollback() # 에러 발생 시 자동 롤백
        raise e
    finally:
        await session.close() # 연결 해제