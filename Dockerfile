FROM python:3.12-slim-bookworm

ARG APP_VERSION=0.1.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    XDG_CACHE_HOME=/models \
    MOONSHINE_VOICE_CACHE=/models \
    VSS_DATA_DIR=/data

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libportaudio2 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /models \
    && chown app:app /data /models

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

USER app
EXPOSE 8000
VOLUME ["/data", "/models"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "subtitle_studio.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
