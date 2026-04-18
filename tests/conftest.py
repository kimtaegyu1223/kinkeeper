import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

from shared.models import Base


@pytest.fixture(scope="session")
def pg_container():
    """테스트 세션 동안 PostgreSQL 컨테이너를 하나 띄움."""
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres


@pytest.fixture(scope="session")
def db_engine(pg_container: PostgresContainer):
    """컨테이너 URL로 엔진 생성 + 마이그레이션 대신 테이블 직접 생성."""
    # testcontainers는 기본으로 psycopg2 URL을 반환하므로 psycopg3용으로 교체
    url = pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql+psycopg")
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """테스트 하나마다 트랜잭션을 열고, 끝나면 rollback해서 DB 초기화."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, expire_on_commit=False)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
