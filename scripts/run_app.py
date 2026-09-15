from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import venv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
ENVIRONMENT_MARKER = VENV_DIR / ".video-subtitle-studio-dependencies"
DEPENDENCY_FILE = REPO_ROOT / "pyproject.toml"


def venv_python() -> Path:
    executable = "python.exe" if sys.platform == "win32" else "python"
    directory = "Scripts" if sys.platform == "win32" else "bin"
    return VENV_DIR / directory / executable


def dependency_fingerprint() -> str:
    return hashlib.sha256(DEPENDENCY_FILE.read_bytes()).hexdigest()


def environment_is_ready(python: Path) -> bool:
    if not python.is_file() or not ENVIRONMENT_MARKER.is_file():
        return False
    if ENVIRONMENT_MARKER.read_text(encoding="utf-8").strip() != dependency_fingerprint():
        return False
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import fastapi, subtitle_studio, uvicorn",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def prepare_environment() -> Path:
    if sys.version_info < (3, 11):
        raise RuntimeError("Video Subtitle Studio requires Python 3.11 or newer")

    python = venv_python()
    if not python.is_file():
        print(f"Creating Python environment at {VENV_DIR}", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)

    if not environment_is_ready(python):
        print("Installing application dependencies", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-e", ".[test]"],
            cwd=REPO_ROOT,
            check=True,
        )
        ENVIRONMENT_MARKER.write_text(
            dependency_fingerprint() + "\n",
            encoding="utf-8",
        )
    else:
        print("Python environment is ready", flush=True)
    return python


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the Python environment and run Video Subtitle Studio"
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Prepare the environment without starting the server",
    )
    args = parser.parse_args()

    python = prepare_environment()
    if args.setup_only:
        return 0

    return subprocess.call(
        [
            str(python),
            "-m",
            "uvicorn",
            "subtitle_studio.api:app",
            "--app-dir",
            "src",
            "--reload",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        cwd=REPO_ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
