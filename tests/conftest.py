import pytest


# testcontainers postgres fixture will be added in PR #2 (shared/db.py)
# Placeholder to keep pytest happy for now
@pytest.fixture(scope="session")
def placeholder() -> None:
    pass
