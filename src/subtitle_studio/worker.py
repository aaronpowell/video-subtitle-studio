import logging
import queue
import threading

from .models import JobStatus
from .processing import JobProcessor
from .storage import JobNotFoundError, Storage

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, storage: Storage, processor: JobProcessor):
        self.storage = storage
        self.processor = processor
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="transcription-worker", daemon=True
        )
        self._thread.start()
        for job_id in self.storage.recover_interrupted_jobs():
            self.enqueue(job_id)

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def stop(self) -> bool:
        if self._thread is None:
            return True
        self._queue.put(None)
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            logger.warning("transcription worker did not stop within 10 seconds")
            return False
        self._thread = None
        return True

    def wait_until_idle(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                job = self.storage.get_job(job_id)
                self.processor.process(job, self.storage)
            except JobNotFoundError:
                logger.warning("queued job no longer exists: %s", job_id)
            except Exception as error:
                logger.exception("job failed: %s", job_id)
                try:
                    self.storage.set_status(
                        str(job_id), JobStatus.FAILED, self._safe_error(error)
                    )
                except JobNotFoundError:
                    logger.warning("failed job no longer exists: %s", job_id)
            finally:
                self._queue.task_done()

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).strip() or error.__class__.__name__
        return message[:2000]
