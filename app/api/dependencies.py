from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.identity import AuthenticatedUser, AuthenticationRequired


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "authentication_required",
            "message": "Authentication required",
        },
    )


def current_user(request: Request) -> AuthenticatedUser:
    try:
        return request.app.state.identity_service.authenticate(
            request.cookies.get("daibm_session")
        )
    except AuthenticationRequired as error:
        raise _authentication_error() from error


def require_roles(
    *roles: str,
) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    def dependency(
        user: Annotated[AuthenticatedUser, Depends(current_user)],
    ) -> AuthenticatedUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden_role",
                    "message": "Current role cannot perform this action",
                },
            )
        return user

    return dependency
