"""HTTP adapters for the application services."""

from app.api.facility import router as facility_router

__all__ = ["facility_router"]
