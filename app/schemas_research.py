from uuid import UUID

from pydantic import BaseModel, Field


class ResearchInferenceRequest(BaseModel):
    enterprise_id: str = Field(pattern=r"^E\d{4}$")
    graph_snapshot_id: UUID
    model_version_id: UUID
