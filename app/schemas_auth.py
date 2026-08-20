from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def username_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("username must not be blank")
        return normalized


class CurrentUserResponse(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    role: str
    organization_id: UUID
    organization_code: str
    organization_name: str


class LoginResponse(BaseModel):
    user: CurrentUserResponse
    expires_at: datetime


class DemoAccountResponse(BaseModel):
    username: str
    display_name: str
    role: str
    organization_code: str
