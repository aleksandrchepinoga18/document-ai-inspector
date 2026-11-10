"""HTTP entry point for DOCX checks, safe correction and PDF export."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.documents import router
from backend.api.upload_limit import UploadLimitMiddleware
from backend.core.config import Settings, get_settings
from backend.documents.errors import DocumentError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name, version="0.6.0")
    application.state.settings = settings
    application.add_middleware(UploadLimitMiddleware, max_bytes=settings.max_upload_bytes + 64 * 1024)
    application.include_router(router)

    @application.exception_handler(DocumentError)
    async def document_error(request: Request, exc: DocumentError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
