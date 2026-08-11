#!/usr/bin/env python3
"""Local web UI for the fantasy agent.

Deliberately built on the standard library. A single-user dashboard on localhost
does not need a framework, and adding one would mean three more dependencies
plus a version treadmill on a tool whose entire value is being reliable on a
Sunday morning. `ThreadingHTTPServer` handles this load without noticing.

Two things need more than plain request/response:

  * Slow work (a full sync, a draft simulation) runs as a background job and the
    browser polls for progress, so a 35 second sync does not look like a hang.
  * The live draft pushes updates over Server-Sent Events, which is one long
    response and needs no extra protocol.

Run:  python -m webapp.server        (or: python cli.py web)
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import queue
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from webapp import api

log = logging.getLogger("webapp")

STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8770

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


# --------------------------------------------------------------- job runner


class Job:
    __slots__ = ("id", "name", "status", "progress", "result", "error", "started", "ended")

    def __init__(self, job_id: str, name: str) -> None:
        self.id = job_id
        self.name = name
        self.status = "running"
        self.progress = "Starting..."
        self.result: object = None
        self.error: str | None = None
        self.started = time.time()
        self.ended: float | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.ended or time.time()) - self.started, 1),
        }


class JobRunner:
    """Fire-and-poll background work.

    Jobs are kept for a while after finishing so a browser that reloads
    mid-simulation can still collect the result instead of silently re-running
    a ten second job.
    """

    RETAIN_SECONDS = 900

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn, *args, **kwargs) -> Job:
        job = Job(secrets.token_urlsafe(9), name)
        with self._lock:
            self._reap()
            self._jobs[job.id] = job

        def report(message: str) -> None:
            job.progress = message

        def run() -> None:
            try:
                job.result = fn(*args, progress=report, **kwargs)
                job.status = "done"
                job.progress = "Complete."
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                log.exception("job %s (%s) failed", job.id, name)
            finally:
                job.ended = time.time()

        threading.Thread(target=run, name=f"job-{name}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _reap(self) -> None:
        cutoff = time.time() - self.RETAIN_SECONDS
        for jid, job in list(self._jobs.items()):
            if job.ended and job.ended < cutoff:
                del self._jobs[jid]


JOBS = JobRunner()


# ------------------------------------------------------------------ routing

ROUTES: dict[tuple[str, str], object] = {}


def route(method: str, path: str):
    def wrap(fn):
        ROUTES[(method, path)] = fn
        return fn

    return wrap


def _int(params: dict, key: str, default=None):
    raw = params.get(key, [None])[0]
    if raw in (None, "", "null", "undefined"):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _str(params: dict, key: str, default: str = "") -> str:
    return (params.get(key, [default])[0] or default).strip()


# --- read-only endpoints -----------------------------------------------------

@route("GET", "/api/bootstrap")
def _bootstrap(params, body):
    return api.bootstrap()


@route("GET", "/api/health")
def _health(params, body):
    return api.health()


@route("GET", "/api/board")
def _board(params, body):
    return api.board(_str(params, "position"), _int(params, "top", 300))


@route("GET", "/api/draft/state")
def _draft_state(params, body):
    return api.draft_state(_int(params, "slot"))


@route("GET", "/api/draft/dissent")
def _dissent(params, body):
    return api.draft_dissent(_int(params, "top", 20))


@route("GET", "/api/draft/recap")
def _recap(params, body):
    return api.draft_recap()


@route("GET", "/api/draft/simulate/last")
def _last_sim(params, body):
    return api.last_simulation()


@route("GET", "/api/lineup")
def _lineup(params, body):
    return api.lineup(_int(params, "week"))


@route("GET", "/api/startsit")
def _startsit(params, body):
    return api.startsit(_int(params, "week"))


@route("GET", "/api/matchup")
def _matchup(params, body):
    return api.matchup(_int(params, "week"))


@route("GET", "/api/standings")
def _standings(params, body):
    return api.league_standings()


@route("GET", "/api/byes")
def _byes(params, body):
    return api.byes(_int(params, "through", 17))


@route("GET", "/api/activity")
def _activity(params, body):
    return api.activity()


@route("GET", "/api/trending")
def _trending(params, body):
    return api.trending(_str(params, "kind", "add"), _int(params, "hours", 48))


@route("GET", "/api/trade/targets")
def _trade_targets(params, body):
    return api.trade_targets(_int(params, "week"), _int(params, "top", 6))


@route("GET", "/api/players/search")
def _search(params, body):
    return api.search_players(_str(params, "q"))


@route("GET", "/api/player")
def _player(params, body):
    return api.player_detail(_str(params, "id"), _int(params, "week"))


@route("GET", "/api/compare")
def _compare(params, body):
    ids = [i for i in _str(params, "ids").split(",") if i]
    return api.compare_players(ids, _int(params, "week"))


# --- mutating / slow endpoints (jobs) ---------------------------------------

@route("POST", "/api/sync")
def _sync(params, body):
    job = JOBS.submit("sync", api.do_sync, bool(body.get("full")))
    return {"job": job.as_dict()}


@route("POST", "/api/draft/simulate")
def _simulate(params, body):
    job = JOBS.submit(
        "draft-simulate",
        api.draft_simulate,
        int(body.get("candidates") or 8),
        int(body.get("trials") or 200),
        body.get("slot") or None,
    )
    return {"job": job.as_dict()}


@route("POST", "/api/draft/plan")
def _plan(params, body):
    job = JOBS.submit("draft-plan", api.draft_plan, int(body.get("trials") or 150))
    return {"job": job.as_dict()}


@route("POST", "/api/waivers")
def _waivers(params, body):
    job = JOBS.submit(
        "waivers", api.waivers, body.get("week") or None, int(body.get("top") or 12)
    )
    return {"job": job.as_dict()}


@route("POST", "/api/digest")
def _digest(params, body):
    job = JOBS.submit("digest", api.digest, body.get("week") or None)
    return {"job": job.as_dict()}


@route("POST", "/api/digest/notify")
def _digest_notify(params, body):
    return api.notify_digest(body.get("week") or None)


@route("POST", "/api/trade/evaluate")
def _trade_eval(params, body):
    return api.trade_evaluate(
        body.get("send") or [],
        body.get("receive") or [],
        body.get("partner") or None,
        body.get("week") or None,
    )


@route("GET", "/api/jobs")
def _job_status(params, body):
    job = JOBS.get(_str(params, "id"))
    if job is None:
        return {"error": "unknown job", "status": "gone"}
    return job.as_dict()


# --------------------------------------------------------------------- SSE


class DraftFeed:
    """One poller for the live draft, fanned out to every open browser tab.

    Polling Sleeper once per connected tab would multiply requests for no
    reason, so a single background thread polls and pushes to subscribers.
    """

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._interval = 5.0
        self._slot: int | None = None

    def subscribe(self, slot: int | None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            self._slot = slot or self._slot
            self._subs.append(q)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._poll, name="draft-feed", daemon=True
                )
                self._thread.start()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, payload: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def _poll(self) -> None:
        last = None
        while True:
            with self._lock:
                if not self._subs:
                    return
                slot = self._slot
            try:
                state = api.draft_state(slot)
                key = (state.get("picks_made"), state.get("status"))
                if key != last:
                    last = key
                    self._publish({"type": "draft", "state": state})
                else:
                    self._publish({"type": "ping", "at": time.time()})
            except Exception as exc:  # noqa: BLE001
                self._publish({"type": "error", "message": str(exc)})
            time.sleep(self._interval)


FEED = DraftFeed()


# ------------------------------------------------------------------ handler


class Handler(BaseHTTPRequestHandler):
    server_version = "SleeperFantasyUI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default
        if os.environ.get("WEB_VERBOSE"):
            log.info("%s - %s", self.address_string(), fmt % args)

    # -- helpers

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, ctype: str, status: int = 200, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (STATIC_ROOT / rel).resolve()
        # Never serve outside the static root, however creative the path is.
        if not str(target).startswith(str(STATIC_ROOT.resolve())):
            self._send_bytes(b"forbidden", "text/plain", 403)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            # Unknown paths fall through to the SPA shell so deep links work.
            target = STATIC_ROOT / "index.html"
        ctype, _ = mimetypes.guess_type(str(target))
        self._send_bytes(
            target.read_bytes(), ctype or "application/octet-stream", 200, "no-cache"
        )

    def _sse(self, params) -> None:
        slot = _int(params, "slot")
        q = FEED.subscribe(slot)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                chunk = f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            FEED.unsubscribe(q)

    # -- verbs

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)

        if path == "/api/events/draft":
            self._sse(params)
            return

        handler = ROUTES.get(("GET", path))
        if handler is None:
            if path.startswith("/api/"):
                self._send_json({"error": f"no route for {path}"}, 404)
            else:
                self._static(path)
            return
        self._dispatch(handler, params, {})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, 400)
            return

        handler = ROUTES.get(("POST", path))
        if handler is None:
            self._send_json({"error": f"no route for {path}"}, 404)
            return
        self._dispatch(handler, params, body)

    def _dispatch(self, handler, params, body) -> None:
        try:
            self._send_json(handler(params, body))
        except Exception as exc:  # noqa: BLE001
            log.exception("handler failed: %s", self.path)
            self._send_json(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "where": self.path,
                    "traceback": traceback.format_exc(limit=6).splitlines()[-6:],
                },
                500,
            )


def serve(port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = True) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"

    print(f"\n  Sleeper Fantasy Agent")
    print(f"  {url}")
    print(f"  Ctrl-C to stop\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


def main() -> None:
    p = argparse.ArgumentParser(description="Web UI for the Sleeper fantasy agent")
    p.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", DEFAULT_PORT)))
    p.add_argument(
        "--host",
        default=os.environ.get("WEB_HOST", "127.0.0.1"),
        help="defaults to localhost; the server is unauthenticated, so binding "
        "it to 0.0.0.0 exposes your league data to your whole network",
    )
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    serve(args.port, args.host, not args.no_browser)


if __name__ == "__main__":
    main()
