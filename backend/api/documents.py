"""Document inspection, safe correction, report and artifact retrieval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.core.schemas import DocumentReport, InspectionOptions
from backend.documents.errors import DocumentError
from backend.documents.profiles import list_profiles, load_profile
from backend.documents.security import DOCX_MIME, validate_upload_metadata
from backend.pipeline.checker import check_document, read_artifact, read_report
from backend.llm.runner import validate_selection


router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/options", response_model=InspectionOptions)
def options(request: Request) -> InspectionOptions:
    settings = request.app.state.settings
    return InspectionOptions(profiles=list_profiles(), max_upload_bytes=settings.max_upload_bytes,
                             processing_timeout_seconds=settings.processing_timeout_seconds)


@router.post("/check", response_model=DocumentReport, status_code=201)
async def check(
    request: Request,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str, Form()] = "default",
    provider: Annotated[str, Form()] = "none",
    use_llm: Annotated[bool, Form()] = False,
    auto_fix: Annotated[bool, Form()] = False,
    render_pdf: Annotated[bool, Form()] = False,
    allow_cloud: Annotated[bool, Form()] = False,
    synthetic_data: Annotated[bool, Form()] = False,
) -> DocumentReport:
    try:
        validate_selection(use_llm, provider, allow_cloud, synthetic_data)
        selected_profile = load_profile(profile)
        validate_upload_metadata(file.filename, file.content_type)
        settings = request.app.state.settings
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise DocumentError("file_too_large", "Превышен лимит размера DOCX.", 413)
        return await run_in_threadpool(
            check_document, data, file.filename, selected_profile, settings,
            auto_fix=auto_fix, render_pdf=render_pdf,
            use_llm=use_llm, provider=provider, allow_cloud=allow_cloud, synthetic_data=synthetic_data,
        )
    finally:
        await file.close()


@router.get("/{job_id}/report", response_model=DocumentReport)
def report(job_id: UUID, request: Request) -> DocumentReport:
    return read_report(job_id, request.app.state.settings)


@router.get("/{job_id}/download")
def download(job_id: UUID, request: Request, original: bool = False) -> Response:
    content, name = read_artifact(job_id, request.app.state.settings, original=original)
    return Response(content, media_type=DOCX_MIME, headers={
        "Content-Disposition": f'attachment; filename="{job_id}-{name}"',
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/{job_id}/preview")
def preview(job_id: UUID, request: Request) -> Response:
    content, _ = read_artifact(job_id, request.app.state.settings, preview=True)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{job_id}-preview.pdf"',
        "X-Content-Type-Options": "nosniff",
    })
