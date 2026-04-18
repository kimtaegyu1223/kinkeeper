from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shared.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # 끊어진 커넥션 자동 감지·재연결
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """요청당 세션 1개. 정상 종료 시 commit, 예외 시 rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db_connection() -> bool:
    """/healthz 엔드포인트용 DB 핑."""
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
