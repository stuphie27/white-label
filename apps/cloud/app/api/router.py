from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.session import database_is_ready

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {"ok": True, "service": "pirouette-cloud", "version": settings.version}


@router.get("/readyz")
async def readyz(request: Request):
    settings = request.app.state.settings
    engine = getattr(request.app.state, "engine", None)
    database_ready = bool(engine and database_is_ready(engine))
    payload = {
        "ready": True,
        "environment": settings.environment,
        "version": settings.version,
        "database": "ready" if database_ready else "not-configured-or-unavailable",
    }
    return JSONResponse(payload, status_code=200)
