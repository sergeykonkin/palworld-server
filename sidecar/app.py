#!/usr/bin/env python3
"""Read-only JSON sidecar for the Palworld dedicated server.

Polls the game's REST API for the player list and watches the auto-pause
flag file on the (read-only) data bind mount. Serves:

  GET /             dashboard page (index.html, polls the JSON API)
  GET /api/status   full snapshot: reachability, pause state (yes/no/unknown),
                    info, metrics, online players with online_since
  GET /api/events   ring buffer of join/leave/pause/resume events (?limit=N)
  GET /healthz      liveness

Pause state semantics: "no" when someone is online or a resume/join was
observed since sidecar start, "yes" after an observed auto-pause flag file
appearance with no resume since, "unknown" when nothing has been observed
yet and nobody is online.

Never exposes player IPs. Stdlib only.
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REST_URL = os.environ.get("PALWORLD_REST_URL", "http://palworld:8212").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CONNECT_ADDRESS = os.environ.get("CONNECT_ADDRESS", "")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
REST_TIMEOUT = float(os.environ.get("REST_TIMEOUT", "3"))
PAUSE_FILE = Path(os.environ.get("PAUSE_FILE", "/state/.paused"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
API_TOKEN = os.environ.get("API_TOKEN") or None
EVENT_BUFFER = int(os.environ.get("EVENT_BUFFER", "500"))
FAIL_THRESHOLD = int(os.environ.get("FAIL_THRESHOLD", "3"))
INFO_TTL = float(os.environ.get("INFO_TTL", "300"))

_ZERO_IDS = {"0" * 8, "0" * 32}

INDEX_HTML = Path(__file__).with_name("index.html")


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utcnow_iso():
    return iso(datetime.now(timezone.utc))


def rest_get(path):
    req = urllib.request.Request(
        f"{REST_URL}{path}",
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode(),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=REST_TIMEOUT) as resp:
        return json.load(resp)


def player_snapshot(p, online_since):
    """Shape a REST player record for the API. Never includes the IP."""
    return {
        "name": p.get("name"),
        "account": p.get("accountName"),
        "user_id": p.get("userId"),
        "player_id": p.get("playerId"),
        "level": p.get("level"),
        "ping": round(p["ping"], 1) if isinstance(p.get("ping"), (int, float)) else None,
        "online_since": online_since,
    }


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.players = {}  # user_id -> snapshot dict
        self.events = deque(maxlen=EVENT_BUFFER)
        self.paused = False  # flag file currently present (transition detection)
        self.evidence = None  # None | "yes" | "no": last clear pause evidence
        self.paused_since = None
        self.reachable = None  # None = unknown yet
        self.fail_streak = 0
        self.info = None
        self.info_fetched_at = 0.0
        self.metrics = None
        self.started_at = utcnow_iso()

    def event(self, type_, **fields):
        entry = {"ts": utcnow_iso(), "type": type_}
        entry.update(fields)
        self.events.append(entry)
        log(f"event: {json.dumps(entry)}")
        return entry


def paused_state(st):
    """Three-state pause status. Call with st.lock held.

    "no"      someone is online, or a resume/join was observed since start
    "yes"     auto-pause flag file appeared and no resume observed since
    "unknown" nothing observed yet and nobody online
    """
    if st.players:
        return "no"
    return st.evidence or "unknown"


def tick(st):
    """One poll cycle: pause file first, REST second (it freezes while paused)."""
    paused_now = PAUSE_FILE.exists()
    with st.lock:
        if paused_now and not st.paused:
            st.paused = True
            st.evidence = "yes"
            try:
                st.paused_since = iso(
                    datetime.fromtimestamp(PAUSE_FILE.stat().st_mtime, timezone.utc)
                )
            except OSError:
                st.paused_since = utcnow_iso()
            st.event("pause", since=st.paused_since)
        elif not paused_now and st.paused:
            st.paused = False
            st.evidence = "no"
            st.event("resume", paused_since=st.paused_since)
            st.paused_since = None

    if paused_now:
        return  # REST is SIGSTOP-frozen while paused; do not poke it.

    try:
        data = rest_get("/v1/api/players")
    except Exception as exc:
        with st.lock:
            st.fail_streak += 1
            if st.reachable is not False and st.fail_streak >= FAIL_THRESHOLD:
                st.reachable = False
                st.event("server_unreachable", detail=f"{type(exc).__name__}: {exc}")
        return

    with st.lock:
        if st.reachable is False:
            st.event("server_reachable")
        st.reachable = True
        st.fail_streak = 0

        now = utcnow_iso()
        current = {}
        for p in data.get("players", []):
            if p.get("playerId") in _ZERO_IDS:
                continue  # still loading / in character creation
            uid = p.get("userId") or p.get("playerId") or p.get("name")
            if uid:
                current[uid] = p

        if current:
            # Observing players online proves the server is not paused.
            st.evidence = "no"

        for uid, p in current.items():
            if uid not in st.players:
                st.players[uid] = player_snapshot(p, now)
                st.event("join", player=p.get("name"), user_id=uid,
                         account=p.get("accountName"), level=p.get("level"))
            else:
                st.players[uid].update(player_snapshot(p, st.players[uid]["online_since"]))

        for uid in list(st.players):
            if uid not in current:
                gone = st.players.pop(uid)
                st.event("leave", player=gone.get("name"), user_id=uid,
                         account=gone.get("account"),
                         online_since=gone.get("online_since"))

    try:
        metrics = rest_get("/v1/api/metrics")
        with st.lock:
            st.metrics = metrics
    except Exception:
        pass

    with st.lock:
        want_info = st.info is None or (time.monotonic() - st.info_fetched_at) > INFO_TTL
    if want_info:
        try:
            info = rest_get("/v1/api/info")
            with st.lock:
                st.info = info
                st.info_fetched_at = time.monotonic()
        except Exception:
            pass


def poller(st):
    while True:
        try:
            tick(st)
        except Exception as exc:
            log(f"poller error: {type(exc).__name__}: {exc}")
        time.sleep(POLL_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    server_version = "palworld-api/1.0"
    protocol_version = "HTTP/1.1"

    def _send_bytes(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send(self, code, obj):
        self._send_bytes(code, json.dumps(obj, indent=2).encode(), "application/json")

    def _authorized(self):
        if API_TOKEN is None:
            return True
        return self.headers.get("Authorization") == f"Bearer {API_TOKEN}"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/healthz":
            return self._send(200, {"ok": True})
        if not self._authorized():
            return self._send(401, {"error": "bearer token required"})

        st = self.server.state
        if path in ("/", "/index.html"):
            try:
                return self._send_bytes(200, INDEX_HTML.read_bytes(),
                                        "text/html; charset=utf-8")
            except OSError:
                return self._send(404, {"error": "dashboard not installed"})
        if path == "/api/status":
            with st.lock:
                players = sorted(st.players.values(), key=lambda p: p["online_since"])
                return self._send(200, {
                    "now": utcnow_iso(),
                    "sidecar_started_at": st.started_at,
                    "connect": CONNECT_ADDRESS or None,
                    "server": {
                        "reachable": st.reachable,
                        "paused": paused_state(st),
                        "paused_since": st.paused_since if st.evidence == "yes" else None,
                        "info": st.info,
                        "metrics": st.metrics,
                    },
                    "players": players,
                })
        if path == "/api/events":
            try:
                limit = min(int(parse_qs(parsed.query).get("limit", [EVENT_BUFFER])[0]), EVENT_BUFFER)
            except ValueError:
                limit = EVENT_BUFFER
            with st.lock:
                events = list(st.events)[-limit:]
            return self._send(200, {"count": len(events), "events": events})

        return self._send(404, {"error": "not found",
                                "routes": ["/api/status", "/api/events", "/healthz"]})

    def log_message(self, fmt, *args):
        log(f"http {self.client_address[0]} {fmt % args}")


def main():
    if not ADMIN_PASSWORD:
        log("WARNING: ADMIN_PASSWORD is empty; REST polling will fail")
    st = State()
    st.event("sidecar_start", rest_url=REST_URL, pause_file=str(PAUSE_FILE))
    threading.Thread(target=poller, args=(st,), daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    httpd.state = st
    log(f"listening on 0.0.0.0:{LISTEN_PORT} (token {'required' if API_TOKEN else 'not set'})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
