from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies import current_user
from app.identity import (
    AuthenticatedUser,
    AuthenticationRequired,
    InvalidCredentials,
)
from app.schemas_auth import (
    CurrentUserResponse,
    DemoAccountResponse,
    LoginRequest,
    LoginResponse,
)


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE_NAME = "daibm_session"
SESSION_SECONDS = 12 * 60 * 60


def _serialize_user(user: AuthenticatedUser) -> CurrentUserResponse:
    return CurrentUserResponse(**asdict(user))


@router.get("/demo-accounts", response_model=list[DemoAccountResponse])
def demo_accounts(request: Request):
    return request.app.state.identity_service.demo_accounts()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    try:
        result = request.app.state.identity_service.login(
            payload.username,
            payload.password,
        )
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "Invalid username or password",
            },
        ) from error
    response.set_cookie(
        key=COOKIE_NAME,
        value=result.token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=False,
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
