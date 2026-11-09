"""Stage 2 multipart inspection and persistent report retrieval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.core.schemas import DocumentReport
from backend.documents.errors import DocumentError
from backend.documents.profiles import load_profile
from backend.documents.security import validate_upload_metadata
from backend.pipeline.checker import check_document, read_report


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/check", response_model=DocumentReport, status_code=201)
async def check(
    request: Request,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str, Form()] = "default",
    provider: Annotated[str, Form()] = "none",
    use_llm: Annotated[bool, Form()] = False,
    auto_fix: Annotated[bool, Form()] = False,
) -> DocumentReport:
    try:
        if use_llm or auto_fix or provider != "none":
            raise DocumentError(
                "unsupported_mode", "Этап 2 поддерживает только provider=none, use_llm=false, auto_fix=false.",
            )
        selected_profile = load_profile(profile)
        validate_upload_metadata(file.filename, file.content_type)
        settings = request.app.state.settings
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise DocumentError("file_too_large", "Превышен лимит размера DOCX.", 413)
        return await run_in_threadpool(check_document, data, file.filename, selected_profile, settings)
    finally:
        await file.close()


@router.get("/{job_id}/report", response_model=DocumentReport)
def report(job_id: UUID, request: Request) -> DocumentReport:
    return read_report(job_id, request.app.state.settings)
