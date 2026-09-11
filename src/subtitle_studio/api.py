import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config import Settings
from .exporters import export_ebu_stl, export_srt, export_webvtt
from .models import CaptionProject, CaptionUpdate, Job, JobStatus, utc_now
from .processing import JobProcessor, MediaProcessor
from .storage import JobNotFoundError, ProjectNotReadyError, Storage
from .transcription import MoonshineTranscriber
from .worker import JobWorker

CHUNK_SIZE = 1024 * 1024
SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/jobs"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send):
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body exceeds the configured upload limit"},
        )
        await response(scope, receive, send)


class RequestBodyTooLarge(Exception):
    pass


def _download_name(source_filename: str, extension: str) -> str:
    stem = Path(source_filename).stem
    safe = SAFE_STEM.sub("-", stem).strip(".-")[:100] or "subtitles"
    return f"{safe}.{extension}"


def create_app(
    settings: Settings | None = None,
    processor: JobProcessor | None = None,
) -> FastAPI:
    settings = settings or Settings()
    storage = Storage(settings.data_dir)
    processor = processor or MediaProcessor(
        MoonshineTranscriber(
            model_arch=settings.moonshine_model_arch,
            chunk_seconds=settings.transcription_chunk_seconds,
        ),
        ffmpeg_path=settings.ffmpeg_path,
        ffmpeg_timeout_seconds=settings.ffmpeg_timeout_seconds,
    )
    worker = JobWorker(storage, processor)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.start()
        yield
        if worker.stop():
            close = getattr(processor, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="Video Subtitle Studio",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.worker = worker
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_upload_bytes + CHUNK_SIZE,
    )
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "media-src 'self' blob:; connect-src 'self'; img-src 'self'"
        )
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/jobs", response_model=list[Job])
    def list_jobs() -> list[Job]:
        return storage.list_jobs()

    @app.post("/api/jobs", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        file: Annotated[UploadFile, File()],
        language: Annotated[str | None, Form()] = None,
    ) -> Job:
        original_name = Path(file.filename or "").name
        if not original_name or original_name != file.filename:
            raise HTTPException(status_code=400, detail="A safe filename is required")
        if Path(original_name).suffix.lower() != ".mp4":
            raise HTTPException(status_code=415, detail="Only .mp4 uploads are supported")
        if file.content_type not in {"video/mp4", "application/mp4", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="Upload must use an MP4 content type")
        selected_language = (language or settings.moonshine_language).strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}(?:_[a-z]{2})?", selected_language):
            raise HTTPException(status_code=400, detail="Invalid language code")

        job = storage.create_job(original_name, selected_language)
        size = 0
        try:
            with job.source_path.open("xb") as destination:
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload exceeds {settings.max_upload_mb} MiB",
                        )
                    destination.write(chunk)
            if size < 12:
                raise HTTPException(status_code=400, detail="Upload is empty or invalid")
            with job.source_path.open("rb") as source:
                header = source.read(12)
            if b"ftyp" not in header[4:12]:
                raise HTTPException(status_code=415, detail="Upload is not an MP4 file")
            storage.finalize_upload(job.id)
        except Exception:
            storage.delete_unfinished_job(job.id)
            raise
        finally:
            await file.close()
        worker.enqueue(job.id)
        return storage.get_job(job.id)

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str) -> Job:
        try:
            return storage.get_job(job_id)
        except (JobNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    @app.get("/api/jobs/{job_id}/captions", response_model=CaptionProject)
    def get_captions(job_id: str) -> CaptionProject:
        try:
            return storage.get_project(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except ProjectNotReadyError as error:
            raise HTTPException(status_code=409, detail="Captions are not ready") from error

    @app.put("/api/jobs/{job_id}/captions", response_model=CaptionProject)
    def update_captions(job_id: str, update: CaptionUpdate) -> CaptionProject:
        try:
            project = storage.get_project(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except ProjectNotReadyError as error:
            raise HTTPException(status_code=409, detail="Captions are not ready") from error
        values = project.model_dump()
        values.update({"cues": update.cues, "updated_at": utc_now()})
        try:
            updated = CaptionProject.model_validate(values)
        except ValidationError as error:
            detail = error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
            raise HTTPException(status_code=422, detail=detail) from error
        storage.save_project(updated)
        return updated

    @app.get("/api/jobs/{job_id}/media")
    def get_media(job_id: str) -> FileResponse:
        try:
            job = storage.get_job(job_id)
        except (JobNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        if not job.source_path.is_file():
            raise HTTPException(status_code=404, detail="Media not found")
        return FileResponse(
            job.source_path,
            media_type="video/mp4",
            filename=_download_name(job.source_filename, "mp4"),
        )

    def completed_project(job_id: str) -> CaptionProject:
        try:
            job = storage.get_job(job_id)
            if job.status != JobStatus.COMPLETED:
                raise HTTPException(status_code=409, detail="Captions are not ready")
            return storage.get_project(job_id)
        except (JobNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except ProjectNotReadyError as error:
            raise HTTPException(status_code=409, detail="Captions are not ready") from error

    @app.get("/api/jobs/{job_id}/export.srt")
    def get_srt(job_id: str) -> Response:
        project = completed_project(job_id)
        return Response(
            export_srt(project),
            media_type="application/x-subrip",
            headers={
                "Content-Disposition": f'attachment; filename="{_download_name(project.source_filename, "srt")}"'
            },
        )

    @app.get("/api/jobs/{job_id}/export.vtt")
    def get_vtt(job_id: str) -> Response:
        project = completed_project(job_id)
        return Response(
            export_webvtt(project),
            media_type="text/vtt; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{_download_name(project.source_filename, "vtt")}"'
            },
        )

    @app.get("/api/jobs/{job_id}/export.stl")
    def get_stl(job_id: str) -> Response:
        project = completed_project(job_id)
        try:
            body = export_ebu_stl(
                project,
                frame_rate=settings.ebu_frame_rate,
                chars_per_line=settings.ebu_chars_per_line,
                max_lines=settings.ebu_max_lines,
            )
        except (UnicodeEncodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(
            body,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{_download_name(project.source_filename, "stl")}"'
            },
        )

    return app


app = create_app()
