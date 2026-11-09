"""Serves the evidence site from memory and reruns one probe on both engines on request."""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import unquote

from archaeologist.config import PROBES_DIR, source_client, target_client
from archaeologist.evidence_site import build
from archaeologist.probe_runner import diff_probe

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8"}


def _load_probe(profile: str, probe_id: str) -> dict:
    probes = json.loads((PROBES_DIR / f"{profile}.json").read_text())
    return next(probe for probe in probes if probe["probe_id"] == probe_id)


def default_runner(probe_id: str, profile: str = "shoplog") -> dict:
    """Uses its own database names, so a Challenge never touches the databases of a pipeline run."""
    probe = _load_probe(profile, probe_id)
    return diff_probe(source_client(), target_client(), probe, namespaces=("chs", "cht"))


class Challenges:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._running: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, probe_id: str, runner: Callable[[str], dict]) -> str:
        with self._lock:
            # One run per probe at a time: they share the chs/cht databases.
            existing = self._running.get(probe_id)
            if existing:
                return existing
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {"status": "running"}
            self._running[probe_id] = job_id

        def work() -> None:
            try:
                state = {"status": "done", "result": runner(probe_id)}
            except Exception as error:  # noqa: BLE001 - any failure is reported to the page
                state = {"status": "error", "error": f"{type(error).__name__}: {error}"}
            with self._lock:
                self._jobs[job_id] = state
                del self._running[probe_id]

        threading.Thread(target=work, daemon=True).start()
        return job_id

    def status(self, job_id: str) -> dict:
        with self._lock:
            return dict(self._jobs.get(job_id, {"status": "unknown"}))


def make_handler(pages: dict[str, str], challenges: Challenges, runner: Callable[[str], dict]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, content_type: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload, default=str), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(self.path.split("?")[0]).lstrip("/")
            if path.startswith("api/challenge/"):
                return self._json(200, challenges.status(path.rsplit("/", 1)[-1]))
            path = path or "index.html"
            if path in pages:
                return self._send(200, pages[path], CONTENT_TYPES[path[path.rfind("."):]])
            self._send(404, "not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            path = unquote(self.path.split("?")[0]).lstrip("/")
            probe_id = path.removeprefix("api/challenge/")
            if not path.startswith("api/challenge/") or f"findings/{probe_id}.html" not in pages:
                return self._json(404, {"error": "unknown probe"})
            self._json(200, {"job_id": challenges.start(probe_id, runner)})

        def log_message(self, format, *args) -> None:  # noqa: A002
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the evidence site with a live Challenge button.")
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    pages = build.render_all(args.profile, live=True)
    runner = lambda probe_id: default_runner(probe_id, args.profile)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(pages, Challenges(), runner))
    print(f"Evidence site for {args.profile} at http://localhost:{args.port}/ (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
