import secrets

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from shared.config import settings

security = HTTPBasic()


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    username_ok = secrets.compare_digest(
        credentials.username.encode(), settings.admin_user.encode()
    )
    try:
        password_ok = bcrypt.checkpw(
            credentials.password.encode(),
            settings.admin_password_hash.encode(),
        )
    except Exception:
        password_ok = False

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 실패",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
