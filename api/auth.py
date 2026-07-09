"""HTTP Basic Auth (ticket B1 design 9) -- a single shared credential pair,
applied uniformly to every endpoint. Explicitly a stopgap, not tenancy: no
per-vessel identity, no roles, no session/token expiry, no rate limiting on
attempts. Real per-vessel auth is ROADMAP.md ticket 1.4.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from api.config import Settings

_security = HTTPBasic()


def make_auth_dependency(config: Settings):
    """Returns a FastAPI dependency bound to `config`'s credentials --
    a closure rather than a global, so tests can point it at a distinct
    `Settings` instance without patching module state."""

    def require_auth(
        credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
    ) -> None:
        user_ok = secrets.compare_digest(credentials.username, config.auth_user)
        password_ok = secrets.compare_digest(credentials.password, config.auth_password)
        if not (user_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    return require_auth
