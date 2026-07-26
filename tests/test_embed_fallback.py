"""The embedding paths no test reached: resident absent, and resident hostile.

Every other suite gets its vectors from the warm resident, so the whole
in-process branch of `providers.local` was unexercised — and a resident keeps
running whatever code it was started with, which is how a fatal `NameError`
in `embedder._model()` sat behind a green suite. That branch is not exotic: it
is what a machine with no running resident takes, which means every first run
and every CI run.

Three outcomes, each in its own subprocess because `config` reads the
environment at import time:

1. resident unreachable + model cached  -> embed in-process, 1024-dim vectors
2. resident unreachable + no model      -> EmbeddingUnavailable, NO download
3. resident alive but refuses our token -> EmbeddingUnavailable, and it must
   NOT fall back (that fallback loads a second 2 GB copy of the model in
   silence, and cost this project hours of false evidence)

Nothing here talks to the shared resident on the default port, so a stale one
cannot make these pass.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Unroutable by RFC 5737, and — the point — not in config._LOOPBACK, so
# `_obtain_socket` refuses to autostart a resident and returns None at once.
# A dead loopback port would instead spawn one and measure the wrong thing.
DEAD_HOST = "192.0.2.1"

_HDR = struct.Struct("!I")

_passed = 0
_failed = 0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def run_child(code: str, env: dict[str, str], timeout: int = 600) -> tuple[int, str, str]:
    """Run a snippet against the repo's src/ with a controlled environment."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO),
        env={**os.environ, "PYTHONUTF8": "1", **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# --------------------------------------------------------------- fixtures


class HostileResident:
    """A resident that answers, and rejects every token.

    Speaks just enough of the wire protocol to be indistinguishable from a
    real one that belongs to somebody else.
    """

    def __init__(self) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        self._srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                try:
                    conn.settimeout(5.0)
                    raw = conn.recv(_HDR.size)
                    if len(raw) < _HDR.size:
                        continue
                    (n,) = _HDR.unpack(raw)
                    body = bytearray()
                    while len(body) < n:
                        chunk = conn.recv(min(65536, n - len(body)))
                        if not chunk:
                            break
                        body.extend(chunk)
                    payload = json.dumps({"error": "unauthorized"}).encode()
                    conn.sendall(_HDR.pack(len(payload)) + payload)
                except OSError:
                    pass

    def __enter__(self) -> "HostileResident":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._srv.close()
        self._thread.join(timeout=3)


# ------------------------------------------------------------------ cases


_CACHED_OK = """
import sys
sys.path.insert(0, r"{repo}")
from src import embedder
from src.config import EMBED_HOST_IS_LOCAL, EMBEDDING_DIM
from src.providers.local import LocalProvider

assert not EMBED_HOST_IS_LOCAL, "host must be non-loopback or autostart kicks in"

# Direct calls: the ones that would have caught the NameError instantly.
q = embedder.embed_query("перевірка запасного шляху")
p = embedder.embed_passages(["first passage", "друга частина"])
dim = embedder.warmup()

prov = LocalProvider().embed_passages(["through the provider"])

print("QDIM", len(q))
print("PCOUNT", len(p))
print("PDIM", len(p[0]))
print("WARMUP", dim)
print("PROVDIM", len(prov[0]))
print("EXPECTED", EMBEDDING_DIM)
"""

_NO_MODEL = """
import sys
sys.path.insert(0, r"{repo}")
from src.providers.base import EmbeddingUnavailable
from src.providers.local import LocalProvider
from src.config import EMBED_HOST_IS_LOCAL, MODEL_CACHE
from src.embedder import is_model_cached

assert not EMBED_HOST_IS_LOCAL
assert not is_model_cached(), "fixture must present an empty model cache"
try:
    LocalProvider().embed_passages(["no resident, no model"])
except EmbeddingUnavailable as exc:
    print("RAISED", type(exc).__name__)
else:
    print("RAISED none")
files = [p for p in MODEL_CACHE.rglob("*") if p.is_file()] if MODEL_CACHE.exists() else []
print("CACHEFILES", len(files))
"""

_HOSTILE = """
import sys, time
sys.path.insert(0, r"{repo}")
from src.providers.base import EmbeddingUnavailable
from src.providers.local import LocalProvider

start = time.time()
try:
    LocalProvider().embed_passages(["a resident that is not ours"])
except EmbeddingUnavailable as exc:
    print("RAISED", type(exc).__name__)
    print("MSG", str(exc)[:200].replace("\\n", " "))
else:
    print("RAISED none")
print("ELAPSED", round(time.time() - start, 1))
"""


def test_unreachable_with_cached_model() -> None:
    from src.embedder import is_model_cached

    if not is_model_cached():
        print("SKIP  in-process fallback (model not warmed on this machine)")
        return

    rc, out, err = run_child(
        _CACHED_OK.format(repo=REPO), {"MNEMO_EMBED_HOST": DEAD_HOST}
    )
    values = dict(
        line.split(" ", 1) for line in out.splitlines() if " " in line
    )
    check(
        "in-process fallback embeds when no resident is reachable",
        rc == 0,
        detail=(err or out)[-600:],
    )
    if rc != 0:
        return
    expected = values.get("EXPECTED")
    check(
        "embed_query returns a full-dimension vector in-process",
        values.get("QDIM") == expected,
        detail=out,
    )
    check(
        "embed_passages returns one vector per text in-process",
        values.get("PCOUNT") == "2" and values.get("PDIM") == expected,
        detail=out,
    )
    check(
        "warmup works without a resident",
        values.get("WARMUP") == expected,
        detail=out,
    )
    check(
        "LocalProvider falls back to in-process embedding",
        values.get("PROVDIM") == expected,
        detail=out,
    )


def test_unreachable_without_model() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo nomodel ") as raw:
        rc, out, err = run_child(
            _NO_MODEL.format(repo=REPO),
            {"MNEMO_EMBED_HOST": DEAD_HOST, "MNEMO_HOME": raw},
            timeout=120,
        )
        values = dict(
            line.split(" ", 1) for line in out.splitlines() if " " in line
        )
        check(
            "no resident and no model raises instead of hanging",
            rc == 0 and values.get("RAISED") == "EmbeddingUnavailable",
            detail=(err or out)[-600:],
        )
        # The binding invariant: only an explicit `warmup` may fetch the model.
        check(
            "a refused embed downloads nothing",
            values.get("CACHEFILES") == "0",
            detail=out,
        )


def test_live_resident_rejects_token() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo hostile ") as raw:
        token = Path(raw) / "embed.token"
        token.write_text("0" * 48, encoding="utf-8")
        with HostileResident() as fake:
            started = time.time()
            rc, out, err = run_child(
                _HOSTILE.format(repo=REPO),
                {
                    "MNEMO_EMBED_HOST": "127.0.0.1",
                    "MNEMO_EMBED_PORT": str(fake.port),
                    "MNEMO_EMBED_TOKEN_FILE": str(token),
                },
                timeout=300,
            )
            wall = time.time() - started

    values = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    check(
        "a resident that refuses our token is an error, not a fallback",
        rc == 0 and values.get("RAISED") == "EmbeddingUnavailable",
        detail=(err or out)[-600:],
    )
    # Refusing fast is the second, independent proof that no copy was loaded.
    # Measured on this machine: refusal 0.1 s, versus 4.9 s for a real cold
    # in-process load + embed. 2 s sits an order of magnitude above the one
    # and well under the other — a fallback cannot slip through it.
    check(
        "refusal is immediate — no second model copy is loaded",
        wall < 2.0,
        detail=f"{wall:.1f}s, child said {values.get('ELAPSED')}s",
    )


def main() -> int:
    test_unreachable_with_cached_model()
    test_unreachable_without_model()
    test_live_resident_rejects_token()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
