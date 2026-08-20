from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models_identity import OrganizationModel, UserModel, UserSessionModel


class IdentityRepository:
    def get_organization_by_id(
        self,
        session: Session,
        organization_id: uuid.UUID,
    ) -> OrganizationModel | None:
        return session.get(OrganizationModel, organization_id)

    def get_organization_by_code(
        self,
        session: Session,
        code: str,
    ) -> OrganizationModel | None:
        return session.scalar(
            select(OrganizationModel).where(
                OrganizationModel.organization_code == code
            )
        )

    def list_organizations_by_type(
        self,
        session: Session,
        organization_type: str,
    ) -> list[OrganizationModel]:
        return list(
            session.scalars(
                select(OrganizationModel)
                .where(
                    OrganizationModel.organization_type
                    == organization_type
                )
                .order_by(OrganizationModel.organization_code)
            )
        )

    def get_user_by_username(
        self,
        session: Session,
        username: str,
    ) -> tuple[UserModel, OrganizationModel] | None:
        row = session.execute(
            select(UserModel, OrganizationModel)
            .join(
                OrganizationModel,
                UserModel.organization_id == OrganizationModel.organization_id,
            )
            .where(UserModel.username == username)
        ).one_or_none()
        return (row[0], row[1]) if row else None

    def get_identity_by_token_hash(
        self,
        session: Session,
        token_hash: str,
    ) -> tuple[UserSessionModel, UserModel, OrganizationModel] | None:
        row = session.execute(
            select(UserSessionModel, UserModel, OrganizationModel)
            .join(UserModel, UserSessionModel.user_id == UserModel.user_id)
            .join(
                OrganizationModel,
                UserModel.organization_id == OrganizationModel.organization_id,
            )
            .where(UserSessionModel.token_hash == token_hash)
        ).one_or_none()
        return (row[0], row[1], row[2]) if row else None

    def delete_session(self, session: Session, token_hash: str) -> None:
        session.execute(
            delete(UserSessionModel).where(
                UserSessionModel.token_hash == token_hash
            )
        )
