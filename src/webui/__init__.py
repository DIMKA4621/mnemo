"""Static web cabinet (FR-7, phase 6).

The UI is a thin client over the HTTP API and the WebSocket progress channel;
it holds no memory logic of its own. Everything it needs is under `static/`
and is served as plain files — no build step, no bundler, no CDN.

Mounting (owned by `src/api.py`, service-dev):

    from src.webui import STATIC_DIR
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

`html=True` makes `/ui/` resolve to `index.html`. The static mount is the one
place under the API that is deliberately token-free (contract 9.1: assets, not
data); every `/api` call the page makes carries the Bearer token itself.
"""

from pathlib import Path

__all__ = ["STATIC_DIR", "INDEX_FILE"]

#: Directory to mount at `/ui`.
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

#: Entry document inside :data:`STATIC_DIR`.
INDEX_FILE: Path = STATIC_DIR / "index.html"
