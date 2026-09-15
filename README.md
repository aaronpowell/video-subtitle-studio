# Video Subtitle Studio

Video Subtitle Studio is a self-hosted, single-user web application for turning
MP4 video into editable subtitles. It extracts mono audio with FFmpeg,
transcribes locally with the current
[`moonshine-voice`](https://github.com/moonshine-ai/moonshine) streaming API,
previews synchronized captions in the browser, and exports SRT, WebVTT, and
binary EBU STL files.

Media, transcripts, and model files remain on the host. The application does not
call a hosted transcription service.

## Features

- Validated MP4 upload with configurable size and FFmpeg timeout limits
- Persistent SQLite job queue and per-project asset directories
- One background transcription worker, so requests return immediately
- Explicit queued, extracting, transcribing, completed, and failed states
- Browser video preview with a live text track generated from edited cues
- Caption text and millisecond timing editing
- UTF-8 SRT and WebVTT export
- Genuine binary EBU Tech 3264 STL export with GSI and TTI blocks
- Container health check, non-root runtime, and separate data/model volumes

## Quick start with Docker Compose

```bash
docker compose up --build
```

Open <http://localhost:8000>. The first transcription downloads the selected
Moonshine model into the `moonshine-models` volume and is therefore slower.

The compose file uses the GHCR-ready image name
`ghcr.io/aaronpowell/video-subtitle-studio:latest`. Remove `build:`, or run
`docker compose pull`, when using a published image.

## Local development

Requirements:

- Python 3.11 or 3.12
- FFmpeg available on `PATH`
- A platform supported by `moonshine-voice`

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
uvicorn subtitle_studio.api:app --reload
```

Run the test suite with:

```bash
pytest
```

Tests use fake processors and do not download a speech model.

The GitHub Copilot app configuration uses `scripts/run_app.py` to create an
isolated `.venv`, install the application and test dependencies, and run the
development server. You can use the same bootstrap locally:

```bash
python scripts/run_app.py
```

## Configuration

Settings use the `VSS_` environment prefix.

| Variable | Default | Description |
| --- | --- | --- |
| `VSS_DATA_DIR` | `./data` | SQLite database and uploaded/generated project assets |
| `VSS_MAX_UPLOAD_MB` | `2048` | Maximum accepted upload size |
| `VSS_FFMPEG_PATH` | `ffmpeg` | FFmpeg executable |
| `VSS_FFMPEG_TIMEOUT_SECONDS` | `7200` | Audio extraction timeout |
| `VSS_MOONSHINE_LANGUAGE` | `en` | Default two- or three-letter Moonshine language code |
| `VSS_MOONSHINE_MODEL_ARCH` | unset | Optional Moonshine model architecture; unset uses the language default |
| `VSS_TRANSCRIPTION_CHUNK_SECONDS` | `0.1` | PCM chunk size supplied to Moonshine |
| `VSS_EBU_FRAME_RATE` | `25` | EBU STL frame rate: 25 or 30 |
| `VSS_EBU_CHARS_PER_LINE` | `40` | EBU STL line wrapping width |
| `VSS_EBU_MAX_LINES` | `2` | Maximum wrapped lines per EBU STL cue |

Moonshine uses `MOONSHINE_VOICE_CACHE` when set. The container sets it to
`/models`, which should be mounted persistently to avoid repeated model
downloads.

## Architecture and storage

The frontend is dependency-free HTML, CSS, and JavaScript served by FastAPI.
Uploads stream to disk in bounded chunks. A single in-process worker takes jobs
from a queue, invokes FFmpeg, streams the decoded 16 kHz mono WAV into
Moonshine, and saves the resulting caption project.

SQLite stores job metadata in:

```text
<data-dir>/studio.sqlite3
```

Project assets are stored under generated UUID directories:

```text
<data-dir>/jobs/<job-id>/
  source.mp4
  audio.wav
  project.json
```

`project.json` is the canonical internal representation used by preview and all
exporters. It contains ordered, non-overlapping cues with stable IDs, integer
millisecond start/end times, and text. Interrupted queued or active jobs are
requeued after restart. Run exactly one Uvicorn worker/process; the MVP queue is
intentionally single-process and processes one transcription at a time.

Back up the data volume to preserve projects. Deleting the volume permanently
deletes uploaded media and captions.

## Moonshine timestamps and resource use

The application keeps one active language model loaded, replacing and closing it
when the requested language changes. It uses `Transcriber.create_stream()` and the finalized transcript
returned by `Stream.stop()` from current Moonshine Voice, not Whisper and not
the legacy DialogFlow API. Each cue preserves Moonshine's `start_time` and
`duration` line metadata. This is supported segment-level timing, not fabricated
word-level precision; review and adjust timings before broadcast delivery.

Decoded WAV data is read and normalized in bounded chunks rather than loaded
into memory as a complete Python sample list. CPU and memory requirements still
depend on the chosen model and video duration.
Expect an initial model download and hundreds of megabytes of persistent cache.
CPU-only transcription is supported but may be slower than real time on modest
homelab hardware. Start with Moonshine's default English model, then set
`VSS_MOONSHINE_MODEL_ARCH` only to an architecture supported by the installed
Moonshine release: `tiny`, `base`, `tiny_streaming`, `base_streaming`,
`small_streaming`, or `medium_streaming`.

## Subtitle output

### SRT and WebVTT

SRT uses ordered one-based cue numbers and `HH:MM:SS,mmm` timestamps. WebVTT uses
stable cue IDs and `HH:MM:SS.mmm` timestamps. Both are UTF-8.

### EBU STL

The `.stl` download is a binary EBU Tech 3264 file, not a 3D-printing model. The
writer emits a 1024-byte General Subtitle Information block followed by
128-byte Text and Timing Information blocks. It supports:

- 25 and 30 fps non-drop timecode
- code page 850 GSI header and ISO 6937-2 Latin text table (`CPN=850`, `CCT=00`)
- configurable 40-character wrapping and two-line subtitle limits
- extension TTI blocks when encoded text exceeds 112 bytes
- frame-quantized timings that preserve positive duration without cue overlap

Characters outside the implemented ISO 6937-2 Latin repertoire, excessive
wrapped lines, and projects beyond EBU STL field limits are rejected with a
clear export error rather than silently replaced. The MVP does not support
drop-frame timecode, right-to-left scripts, Unicode EBU STL extensions,
teletext colour/style controls, or programme timecode offsets.
EBU STL metadata export is currently limited to English, German, and Spanish
projects.

## Security and privacy

No authentication is included because this is designed for a trusted,
single-user homelab network. Do not expose it directly to the public internet;
put authentication and TLS at a reverse proxy if remote access is required.
Filenames are reduced to display metadata, storage paths use generated UUIDs,
uploads are checked for MP4 extension/content type/container signature, and
FFmpeg is invoked without a shell. Uploaded media and speech never leave the
host unless the operator separately copies or backs up the data volume.

## Container publishing

CI runs tests and builds the container on pushes to `main` and pull requests.
Tags matching `v*` or published GitHub releases build and push
`linux/amd64` and `linux/arm64` images to GHCR using only `GITHUB_TOKEN`.

## Licence

Video Subtitle Studio is available under the [MIT License](LICENSE). See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for direct dependency and model
licence notes.
