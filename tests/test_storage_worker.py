from pathlib import Path

from subtitle_studio.models import CaptionProject, JobStatus
from subtitle_studio.storage import Storage
from subtitle_studio.worker import JobWorker


class SuccessfulProcessor:
    def process(self, job, storage: Storage) -> None:
        storage.save_project(
            CaptionProject(
                job_id=job.id,
                source_filename=job.source_filename,
                language=job.language,
                duration_ms=1000,
                cues=[],
            )
        )
        storage.set_status(job.id, JobStatus.COMPLETED)


class FailedProcessor:
    def process(self, job, storage: Storage) -> None:
        storage.set_status(job.id, JobStatus.TRANSCRIBING)
        raise RuntimeError("model exploded")


def make_job(storage: Storage):
    job = storage.create_job("video.mp4", "en")
    job.source_path.write_bytes(b"upload")
    return storage.finalize_upload(job.id)


def test_worker_completes_a_job(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    job = make_job(storage)
    worker = JobWorker(storage, SuccessfulProcessor())
    worker.start()
    worker.enqueue(job.id)
    worker.wait_until_idle()
    worker.stop()
    assert storage.get_job(job.id).status == JobStatus.COMPLETED
    assert storage.get_project(job.id).job_id == job.id


def test_worker_records_a_safe_error(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    job = make_job(storage)
    worker = JobWorker(storage, FailedProcessor())
    worker.start()
    worker.enqueue(job.id)
    worker.wait_until_idle()
    worker.stop()
    failed = storage.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error == "model exploded"


def test_interrupted_jobs_are_requeued_on_start(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    job = make_job(storage)
    storage.set_status(job.id, JobStatus.EXTRACTING)
    worker = JobWorker(storage, SuccessfulProcessor())
    worker.start()
    worker.wait_until_idle()
    worker.stop()
    assert storage.get_job(job.id).status == JobStatus.COMPLETED


def test_incomplete_uploads_are_removed_on_start(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    job = storage.create_job("partial.mp4", "en")
    job.source_path.write_bytes(b"partial")
    worker = JobWorker(storage, SuccessfulProcessor())
    worker.start()
    worker.wait_until_idle()
    worker.stop()
    assert storage.list_jobs() == []
    assert not storage.job_dir(job.id).exists()
