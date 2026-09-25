from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import current_user
from app.identity import (
    AccountLocked,
    AuthenticatedUser,
    AuthenticationRequired,
    InvalidCredentials,
)
from app.services.security import WeakPassword
from app.schemas_auth import (
    CurrentUserResponse,
    DemoAccountResponse,
    LoginRequest,
    LoginResponse,
)


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE_NAME = "daibm_session"


def _serialize_user(user: AuthenticatedUser) -> CurrentUserResponse:
    return CurrentUserResponse(**asdict(user))


@router.get("/demo-accounts", response_model=list[DemoAccountResponse])
def demo_accounts(request: Request):
    return request.app.state.identity_service.demo_accounts()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    identity = request.app.state.identity_service
    try:
        result = identity.login(
            payload.username,
            payload.password,
            client_ip=request.client.host if request.client else None,
        )
    except AccountLocked as error:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "account_locked",
                "message": "Too many failed sign-ins; try again later",
            },
        ) from error
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "Invalid username or password",
            },
        ) from error
    settings = identity.settings_provider()
    response.set_cookie(
        key=COOKIE_NAME,
        value=result.token,
        max_age=settings.session_absolute_hours * 3600,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )
    return LoginResponse(
        user=_serialize_user(result.user),
        expires_at=result.expires_at,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    request.app.state.identity_service.logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
    )


@router.get("/session")
def session_probe(request: Request):
    try:
        user = request.app.state.identity_service.authenticate(
            request.cookies.get(COOKIE_NAME)
        )
    except AuthenticationRequired:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": _serialize_user(user).model_dump(mode="json"),
    }


@router.get("/me", response_model=CurrentUserResponse)
def me(
    user: Annotated[AuthenticatedUser, Depends(current_user)],
):
    return _serialize_user(user)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(current_user)],
) -> None:
    try:
        request.app.state.identity_service.change_password(
            user, payload.current_password, payload.new_password
        )
    except WeakPassword as error:
        raise HTTPException(
            status_code=422, detail={"code": "weak_password", "message": str(error)}
        ) from error
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=403,
            detail={"code": "invalid_credentials", "message": "Current password is wrong"},
        ) from error
