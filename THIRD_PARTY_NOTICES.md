# Dependency and model licences

Video Subtitle Studio is MIT-licensed. Its direct runtime dependencies are:

| Dependency | Purpose | Licence |
| --- | --- | --- |
| FastAPI | HTTP API | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| Pydantic / pydantic-settings | Validation and configuration | MIT |
| python-multipart | Multipart upload parsing | Apache-2.0 |
| Moonshine Voice | Local speech recognition | MIT |
| FFmpeg | Media decoding | LGPL-2.1-or-later by default; distribution details vary by build |

Moonshine's current code and default streaming models are MIT-licensed. Upstream
identifies legacy non-streaming, non-English models as exceptions under the
Moonshine Community License. This application uses the current streaming
`moonshine-voice` API; review the upstream `LICENSE` before selecting a custom or
legacy model.

Container images also include transitive Python packages and Debian packages.
Their notices and licence texts are available from their respective package
metadata and installed system documentation.

