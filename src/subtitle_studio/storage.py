import json
import shutil
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from .models import CaptionProject, Job, JobStatus, StoredJob, utc_now


class JobNotFoundError(LookupError):
    pass


class ProjectNotReadyError(LookupError):
    pass


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.db_path = self.data_dir / "studio.sqlite3"
        self._write_lock = threading.Lock()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source_filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create_job(self, source_filename: str, language: str) -> StoredJob:
        job_id = str(uuid4())
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(mode=0o750)
        source_path = job_dir / "source.mp4"
        now = utc_now()
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs
                    (id, source_filename, source_path, language, status, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    job_id,
                    source_filename,
                    str(source_path),
                    language,
                    JobStatus.UPLOADING.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return StoredJob(
            id=job_id,
            source_filename=source_filename,
            source_path=source_path,
            language=language,
            status=JobStatus.UPLOADING,
            created_at=now,
            updated_at=now,
        )

    def delete_unfinished_job(self, job_id: str) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._remove_job_files(job_id)

    def delete_job(self, job_id: str) -> None:
        self.job_dir(job_id)
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            if cursor.rowcount != 1:
                raise JobNotFoundError(job_id)
        self._remove_job_files(job_id)

    def list_jobs(self) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_stored_job(row) for row in rows]

    def get_job(self, job_id: str) -> StoredJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._row_to_stored_job(row)

    def set_status(
        self, job_id: str, status: JobStatus, error: str | None = None
    ) -> StoredJob:
        now = utc_now()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status.value, error, now.isoformat(), job_id),
            )
            if cursor.rowcount != 1:
                raise JobNotFoundError(job_id)
        return self.get_job(job_id)

    def finalize_upload(self, job_id: str) -> StoredJob:
        now = utc_now()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.QUEUED.value,
                    now.isoformat(),
                    job_id,
                    JobStatus.UPLOADING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise JobNotFoundError(job_id)
        return self.get_job(job_id)

    def recover_interrupted_jobs(self) -> list[str]:
        interrupted = (
            JobStatus.QUEUED.value,
            JobStatus.EXTRACTING.value,
            JobStatus.TRANSCRIBING.value,
        )
        now = utc_now().isoformat()
        with self._write_lock, self._connect() as connection:
            incomplete = connection.execute(
                "SELECT id FROM jobs WHERE status = ?",
                (JobStatus.UPLOADING.value,),
            ).fetchall()
            connection.execute(
                "DELETE FROM jobs WHERE status = ?",
                (JobStatus.UPLOADING.value,),
            )
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status IN (?, ?, ?) ORDER BY created_at",
                interrupted,
            ).fetchall()
            connection.execute(
                """
                UPDATE jobs SET status = ?, error = NULL, updated_at = ?
                WHERE status IN (?, ?, ?)
                """,
                (JobStatus.QUEUED.value, now, *interrupted),
            )
        for row in incomplete:
            self._remove_job_files(row["id"])
        return [row["id"] for row in rows]

    def job_dir(self, job_id: str) -> Path:
        if not job_id or any(character not in "0123456789abcdef-" for character in job_id):
            raise ValueError("invalid job id")
        path = (self.jobs_dir / job_id).resolve()
        if path.parent != self.jobs_dir:
            raise ValueError("invalid job path")
        return path

    def save_project(self, project: CaptionProject) -> None:
        destination = self.job_dir(project.job_id) / "project.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(project.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)

    def get_project(self, job_id: str) -> CaptionProject:
        self.get_job(job_id)
        path = self.job_dir(job_id) / "project.json"
        if not path.exists():
            raise ProjectNotReadyError(job_id)
        return CaptionProject.model_validate_json(path.read_text(encoding="utf-8"))

    def _remove_job_files(self, job_id: str) -> None:
        job_dir = self.job_dir(job_id)
        if not job_dir.exists():
            return
        shutil.rmtree(job_dir)

    @staticmethod
    def _row_to_stored_job(row: sqlite3.Row) -> StoredJob:
        return StoredJob(
            id=row["id"],
            source_filename=row["source_filename"],
            source_path=Path(row["source_path"]),
            language=row["language"],
            status=JobStatus(row["status"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
