#!/usr/bin/env python3
"""Serve the dashboard workbench on 127.0.0.1 and feed it snapshot JSON.

    python3 dashboard.py                 # live: collect this machine every --refresh seconds
    python3 dashboard.py --hosts a,b     # live: also collect remote hosts over SSH
    python3 dashboard.py --demo          # fixed web/data/demo.json
    python3 dashboard.py --snapshot f    # fixed snapshot JSON file

Routes: `/` and the static files under ../web, `/api/state` (contract snapshot),
`/api/health` (small JSON liveness + data mode). No external requests are made
by this server; binding is loopback only.
"""

import argparse
import copy
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
WEB_DIR = os.path.join(SKILL_DIR, "web")
DEMO_FILE = os.path.join(WEB_DIR, "data", "demo.json")

DEFAULT_PORT = 8787
DEFAULT_REFRESH_SECONDS = 5
SCHEMA_VERSION = 1

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import collect as collector  # noqa: E402  (imported from this skill's scripts dir)

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

PLACEHOLDER_HTML = """<!doctype html>
<meta charset="utf-8">
<title>dashboard · waiting for web/index.html</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #FAF9F5; color: #1F1E1D;
         font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width: 46rem; padding: 2rem; }
  h1 { font: 400 1.4rem/1.3 ui-serif, Georgia, serif; margin: 0 0 .6rem; }
  code { font: 12px ui-monospace, Menlo, monospace; background: #F4F1EA; padding: .1em .35em; }
  p { color: #6E6A5F; }
  a { color: #B85C3D; }
</style>
<main>
  <h1>dashboard server is up, but <code>web/index.html</code> is missing</h1>
  <p>The local server at this port is serving <code>skills/dashboard/web/</code>. The page
     is added by the UI build; until then the JSON API already works:</p>
  <p><a href="/api/state">/api/state</a> · <a href="/api/health">/api/health</a></p>
</main>
"""


def _now_ms():
    return int(time.time() * 1000)


class StateProvider:
    """Supplies `/api/state`. One data mode per server process."""

    def __init__(self, mode, refresh_seconds=DEFAULT_REFRESH_SECONDS,
                 hosts=(), snapshot_path=None, no_titles=False, home=None):
        self.mode = mode
        self.refresh_seconds = max(1, int(refresh_seconds))
        self.hosts = list(hosts)
        self.snapshot_path = snapshot_path
        self.no_titles = no_titles
        self.home = home
        self._lock = threading.Lock()
        self._cache = None
        self._cache_at = 0
        self._fixed = None
        self._fixed_path = None
        self._demo_mtime = None
        self._last_error = None
        if mode == "snapshot":
            self._fixed = self._read_snapshot(snapshot_path)
            self._fixed_path = snapshot_path
        elif mode == "demo":
            self._demo_mtime = self._demo_mtime_value()
            self._fixed = self._read_demo() or None

    # -- data sources -------------------------------------------------------

    @staticmethod
    def _read_json(path):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or not isinstance(payload.get("machines"), list):
            raise ValueError("%s is not a snapshot object" % path)
        return payload

    def _read_snapshot(self, path):
        try:
            payload = self._read_json(path)
        except (OSError, ValueError) as exc:
            raise SystemExit("cannot read snapshot %s: %s" % (path, exc))
        payload = copy.deepcopy(payload)
        payload["mode"] = "snapshot"
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("generated_at", collector._iso(_now_ms()))
        payload.setdefault("sessions", [])
        self._note(f"snapshot {os.path.basename(path)}")
        return payload

    @staticmethod
    def _demo_mtime_value():
        try:
            return os.path.getmtime(DEMO_FILE)
        except OSError:
            return None

    def _read_demo(self):
        try:
            payload = self._read_json(DEMO_FILE)
        except (OSError, ValueError) as exc:
            self._note("demo.json unavailable: %s" % exc)
            return None
        payload = copy.deepcopy(payload)
        payload.setdefault("mode", "demo")
        payload.setdefault("schema_version", SCHEMA_VERSION)
        return payload

    def _load_demo(self):
        mtime = self._demo_mtime_value()
        if self._fixed is None or mtime != self._demo_mtime:
            self._fixed = self._read_demo()
            self._demo_mtime = mtime
        return self._fixed

    def _note(self, message):
        self._last_error = message

    # -- public -------------------------------------------------------------

    def state(self, force=False):
        if self.mode == "demo":
            demo = self._load_demo()
            if demo is not None:
                return copy.deepcopy(demo)
            return self._error_state("demo data is unavailable (%s)" % self._last_error)
        if self.mode == "snapshot":
            return copy.deepcopy(self._fixed)

        with self._lock:
            age = _now_ms() - self._cache_at
            if not force and self._cache is not None and age < self.refresh_seconds * 1000:
                return copy.deepcopy(self._cache)
            try:
                snapshot = collector.collect_snapshot(
                    hosts=self.hosts,
                    home=self.home,
                    refresh_seconds=self.refresh_seconds,
                    no_titles=self.no_titles,
                )
                self._last_error = None
                if not snapshot.get("sessions"):
                    # Empty stores still deserve a page; demo data carries the warning.
                    demo = self._fallback_demo("no live sessions found; showing demo data")
                    if demo is not None:
                        snapshot = demo
            except Exception as exc:  # collector problems must not kill the page
                self._note("collect failed: %s" % exc)
                self._cache_at = _now_ms()  # retry no sooner than one TTL
                if self._cache is not None:
                    return copy.deepcopy(self._cache)
                demo = self._fallback_demo("live collection failed; showing demo data")
                if demo is not None:
                    return demo
                return self._error_state("live collection failed: %s" % exc)

            self._cache = snapshot
            self._cache_at = _now_ms()
            return copy.deepcopy(snapshot)

    def _fallback_demo(self, warning):
        demo = self._load_demo()
        if demo is None:
            return None
        demo = copy.deepcopy(demo)
        demo["warning"] = warning
        return demo

    def _error_state(self, message):
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "generated_at": collector._iso(_now_ms()),
            "machines": [],
            "sessions": [],
            "error": message,
        }

    def _peek(self):
        """Cached state for health checks; never starts a collection."""
        if self.mode == "demo":
            return self._load_demo()
        if self.mode == "snapshot":
            return self._fixed
        return self._cache

    def health(self):
        snapshot = self._peek() or {}
        sessions = snapshot.get("sessions") or []
        machines = snapshot.get("machines") or []
        index_present = os.path.isfile(os.path.join(WEB_DIR, "index.html"))
        age_ms = _now_ms() - self._cache_at if self._cache_at else None
        payload = {
            "ok": not snapshot.get("error"),
            "mode": snapshot.get("mode", self.mode),
            "schema_version": snapshot.get("schema_version", SCHEMA_VERSION),
            "generated_at": snapshot.get("generated_at"),
            "refresh_seconds": self.refresh_seconds,
            "hosts": self.hosts,
            "sessions": len(sessions),
            "machines": len(machines),
            "agents": sum(len(machine.get("agents") or []) for machine in machines),
            "cache_age_ms": age_ms,
            "web": {"root": WEB_DIR, "index": index_present},
        }
        if self.mode == "live" and self._cache is None:
            payload["warming"] = True
        if snapshot.get("error"):
            payload["ok"] = False
            payload["error"] = snapshot["error"]
        elif self._last_error:
            payload["note"] = self._last_error
        if snapshot.get("warning"):
            payload["warning"] = snapshot["warning"]
        return payload


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "dashboard/1.0"
    protocol_version = "HTTP/1.1"
    quiet = True

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in ("/api/state", "/api/state/"):
            force = "force=1" in (parsed.query or "") or "refresh=1" in (parsed.query or "")
            return self._send_json(self.server.provider.state(force=force))
        if path in ("/api/health", "/api/health/"):
            return self._send_json(self.server.provider.health())
        if path in ("/favicon.ico", "/apple-touch-icon.png"):
            return self._send_empty(204)
        return self._serve_static(path)

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in ("/api/state", "/api/state/"):
            return self._send_json(self.server.provider.state(), body=False)
        if path in ("/api/health", "/api/health/"):
            return self._send_json(self.server.provider.health(), body=False)
        return self._serve_static(path, body=False)

    def _send_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_json(self, payload, status=200, body=True):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(raw)

    # -- static files -------------------------------------------------------

    def _resolve_static(self, path):
        if path in ("", "/"):
            candidate = os.path.join(WEB_DIR, "index.html")
        else:
            relative = path.lstrip("/")
            candidate = os.path.join(WEB_DIR, relative)
            if path.endswith("/"):
                candidate = os.path.join(candidate, "index.html")
        web_root = os.path.realpath(WEB_DIR)
        resolved = os.path.realpath(candidate)
        if resolved != web_root and not resolved.startswith(web_root + os.sep):
            return None
        if os.path.isdir(resolved):
            resolved = os.path.join(resolved, "index.html")
        return resolved if os.path.isfile(resolved) else None

    def _serve_static(self, path, body=True):
        resolved = self._resolve_static(path)
        if resolved is None:
            if path in ("", "/"):
                return self._send_html(PLACEHOLDER_HTML)
            return self._send_json({"error": "not found", "path": path}, status=404)
        try:
            with open(resolved, "rb") as handle:
                raw = handle.read()
        except OSError:
            return self._send_json({"error": "unreadable", "path": path}, status=500)
        extension = os.path.splitext(resolved)[1].lower()
        content_type = MIME_TYPES.get(extension, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(raw)

    def _send_html(self, html, status=200):
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    # -- logging ------------------------------------------------------------

    def log_message(self, fmt, *args):
        if not DashboardHandler.quiet:
            super().log_message(fmt, *args)

    def log_error(self, fmt, *args):
        if not DashboardHandler.quiet:
            super().log_error(fmt, *args)


def _make_server(provider, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.daemon_threads = True
    server.provider = provider
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve the dashboard workbench locally (loopback only).",
    )
    parser.add_argument("--demo", action="store_true", help="serve web/data/demo.json instead of live data")
    parser.add_argument("--snapshot", default=None, help="serve a fixed snapshot JSON file")
    parser.add_argument("--hosts", default="", help="comma-separated SSH hosts to collect in live mode")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port on 127.0.0.1 (default %(default)s)")
    parser.add_argument("--refresh", type=int, default=DEFAULT_REFRESH_SECONDS,
                        help="seconds between live collections (default %(default)s)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser window")
    parser.add_argument("--no-titles", action="store_true", help="hide session titles (privacy)")
    parser.add_argument("--home", default=None, help="alternate home directory (tests)")
    parser.add_argument("--quiet", action="store_true", help="suppress request logging")
    args = parser.parse_args(argv)

    if args.demo and args.snapshot:
        parser.error("--demo and --snapshot are mutually exclusive")

    mode = "demo" if args.demo else ("snapshot" if args.snapshot else "live")
    hosts = [host.strip() for host in args.hosts.split(",") if host.strip()] if mode == "live" else []
    provider = StateProvider(
        mode=mode,
        refresh_seconds=args.refresh,
        hosts=hosts,
        snapshot_path=args.snapshot,
        no_titles=args.no_titles,
        home=args.home,
    )
    DashboardHandler.quiet = args.quiet

    try:
        server = _make_server(provider, max(0, args.port))  # 0 = ephemeral port (tests)
    except OSError as exc:
        raise SystemExit("cannot listen on 127.0.0.1:%s: %s" % (args.port, exc))

    url = "http://127.0.0.1:%d/" % server.server_address[1]
    index_state = "index.html present" if os.path.isfile(os.path.join(WEB_DIR, "index.html")) else "MISSING web/index.html"
    print("dashboard · %s" % url)
    print("  mode      %s%s" % (mode, " (hosts: %s)" % ",".join(hosts) if hosts else ""))
    print("  refresh   %ss" % provider.refresh_seconds)
    print("  web       %s (%s)" % (WEB_DIR, index_state))
    sys.stdout.flush()

    if mode == "live":
        # Warm the cache so the first page view does not wait on collection.
        threading.Thread(target=provider.state, kwargs={"force": True}, daemon=True).start()
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("dashboard · stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
