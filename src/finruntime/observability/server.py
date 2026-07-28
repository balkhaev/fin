from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .control_room import build_runtime_snapshot, snapshot_digest


@dataclass(frozen=True, slots=True)
class ControlRoomConfig:
    frontend_root: Path
    dashboard_path: Path
    runtime_root: Path
    stale_after_seconds: int = 172_800
    incident_limit: int = 100
    poll_seconds: float = 2.0

    def validate(self) -> None:
        if not self.frontend_root.is_dir():
            raise ValueError(f"frontend root does not exist: {self.frontend_root}")
        if not self.dashboard_path.is_file():
            raise ValueError(f"dashboard JSON does not exist: {self.dashboard_path}")
        if self.stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        if self.incident_limit < 1:
            raise ValueError("incident_limit must be positive")
        if self.poll_seconds < 0.2:
            raise ValueError("poll_seconds must be at least 0.2")


def _read_dashboard(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported static dashboard schema")
    return value


def build_dashboard_payload(config: ControlRoomConfig) -> dict[str, Any]:
    historical = _read_dashboard(config.dashboard_path)
    runtime = build_runtime_snapshot(
        config.runtime_root,
        stale_after_seconds=config.stale_after_seconds,
        incident_limit=config.incident_limit,
    )
    result = dict(historical)
    result["runtime"] = runtime
    result["environment"] = dict(historical.get("environment") or {})
    result["environment"].update(
        {
            "control_plane": "read-only",
            "runtime_status": runtime["status"],
            "runtime_strategy_count": runtime["aggregate"]["strategy_count"],
            "runtime_observation_count": runtime["aggregate"]["observation_count"],
            "runtime_incident_count": runtime["aggregate"]["critical_incidents"] + runtime["aggregate"]["warning_incidents"],
            "live_ready": False,
            "exchange_submission_available": False,
            "real_leverage_authorized": False,
        }
    )
    if runtime.get("market_state"):
        result["runtime_market"] = runtime["market_state"]
    return result


class ControlRoomHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: ControlRoomConfig):
        config.validate()
        self.config = config
        self.started_monotonic = time.monotonic()
        super().__init__(address, ControlRoomHandler)


class ControlRoomHandler(BaseHTTPRequestHandler):
    server: ControlRoomHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {format % args}")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'")

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
        etag: str | None = None,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        if etag:
            self.send_header("ETag", f'"{etag}"')
        self._security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _send_json(
        self,
        value: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        head_only: bool = False,
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(payload, content_type="application/json; charset=utf-8", status=status, etag=hashlib.sha256(payload).hexdigest(), head_only=head_only)

    def _runtime(self) -> dict[str, object]:
        return build_runtime_snapshot(
            self.server.config.runtime_root,
            stale_after_seconds=self.server.config.stale_after_seconds,
            incident_limit=self.server.config.incident_limit,
        )

    def _dashboard(self) -> dict[str, Any]:
        return build_dashboard_payload(self.server.config)

    def _health(self) -> dict[str, object]:
        runtime = self._runtime()
        return {
            "service": "fin-control-room",
            "status": runtime["status"],
            "uptime_seconds": round(time.monotonic() - self.server.started_monotonic, 3),
            "runtime_root_exists": runtime["runtime_root_exists"],
            "aggregate": runtime["aggregate"],
            "read_only": True,
            "exchange_submission_available": False,
            "live_ready": False,
        }

    def _serve_api(self, path: str, *, head_only: bool = False) -> bool:
        try:
            if path == "/api/v1/dashboard":
                self._send_json(self._dashboard(), head_only=head_only)
                return True
            if path == "/api/v1/runtime":
                self._send_json(self._runtime(), head_only=head_only)
                return True
            if path == "/api/v1/incidents":
                runtime = self._runtime()
                self._send_json({"schema_version": 1, "generated_at_utc": runtime["generated_at_utc"], "status": runtime["status"], "incidents": runtime["incidents"]}, head_only=head_only)
                return True
            if path == "/api/v1/health":
                self._send_json(self._health(), head_only=head_only)
                return True
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._send_json({"error": "control_room_snapshot_failed", "detail": str(error), "live_ready": False, "exchange_submission_available": False}, status=HTTPStatus.INTERNAL_SERVER_ERROR, head_only=head_only)
            return True
        return False

    def _serve_events(self, query: dict[str, list[str]]) -> None:
        once = query.get("once", ["0"])[0].lower() in {"1", "true", "yes"}
        seconds = min(300.0, max(1.0, float(query.get("seconds", ["60"])[0])))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close" if once else "keep-alive")
        self._security_headers()
        self.end_headers()
        started = time.monotonic()
        last_digest: str | None = None
        try:
            while True:
                payload = self._dashboard()
                digest = snapshot_digest(payload)
                if digest != last_digest:
                    data = json.dumps({"digest": digest, "generated_at_utc": payload.get("generated_at_utc"), "runtime_status": payload.get("runtime", {}).get("status")}, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_digest = digest
                    if once:
                        break
                else:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                if time.monotonic() - started >= seconds:
                    break
                time.sleep(self.server.config.poll_seconds)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            if once:
                self.close_connection = True

    def _static_path(self, request_path: str) -> Path | None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (self.server.config.frontend_root / relative).resolve()
        try:
            candidate.relative_to(self.server.config.frontend_root.resolve())
        except ValueError:
            return None
        if candidate.is_dir():
            candidate = candidate / "index.html"
        return candidate if candidate.is_file() else None

    def _serve_static(self, path: str, *, head_only: bool = False) -> None:
        file_path = self._static_path(path)
        if file_path is None:
            self._send_json({"error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        payload = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send_bytes(payload, content_type=content_type, cache_control="no-cache", etag=hashlib.sha256(payload).hexdigest(), head_only=head_only)

    def _dispatch_get(self, *, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/events":
            if head_only:
                self._send_json({"error": "head_not_supported"}, status=HTTPStatus.METHOD_NOT_ALLOWED, head_only=True)
            else:
                try:
                    self._serve_events(parse_qs(parsed.query))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self.log_error("event stream failed: %s", error)
            return
        if self._serve_api(parsed.path, head_only=head_only):
            return
        self._serve_static(parsed.path, head_only=head_only)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch_get()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch_get(head_only=True)

    def do_POST(self) -> None:  # noqa: N802
        self._send_json({"error": "method_not_allowed", "detail": "FIN Control Room is read-only and exposes no order or mutation endpoints.", "exchange_submission_available": False}, status=HTTPStatus.METHOD_NOT_ALLOWED)


def create_server(
    *,
    host: str,
    port: int,
    frontend_root: str | Path,
    dashboard_path: str | Path,
    runtime_root: str | Path,
    stale_after_seconds: int = 172_800,
    incident_limit: int = 100,
    poll_seconds: float = 2.0,
) -> ControlRoomHTTPServer:
    config = ControlRoomConfig(
        frontend_root=Path(frontend_root).expanduser().resolve(),
        dashboard_path=Path(dashboard_path).expanduser().resolve(),
        runtime_root=Path(runtime_root).expanduser().resolve(),
        stale_after_seconds=stale_after_seconds,
        incident_limit=incident_limit,
        poll_seconds=poll_seconds,
    )
    return ControlRoomHTTPServer((host, port), config)


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only FIN paper/shadow control room.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend-root", default="frontend")
    parser.add_argument("--dashboard", default="frontend/data/dashboard.json")
    parser.add_argument("--runtime-root", default=os.environ.get("FIN_RUNTIME_ROOT", "runtime"))
    parser.add_argument("--stale-after-seconds", type=int, default=172_800)
    parser.add_argument("--incident-limit", type=int, default=100)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args(argv)

    if not _is_loopback(args.host) and not args.allow_remote:
        parser.error("non-loopback binding requires --allow-remote; the server has no authentication layer")

    config = ControlRoomConfig(
        frontend_root=Path(args.frontend_root).expanduser().resolve(),
        dashboard_path=Path(args.dashboard).expanduser().resolve(),
        runtime_root=Path(args.runtime_root).expanduser().resolve(),
        stale_after_seconds=args.stale_after_seconds,
        incident_limit=args.incident_limit,
        poll_seconds=args.poll_seconds,
    )
    config.validate()
    if args.snapshot:
        print(json.dumps(build_dashboard_payload(config), ensure_ascii=False, indent=2, allow_nan=False))
        return 0

    server = ControlRoomHTTPServer((args.host, args.port), config)
    host, port = server.server_address[:2]
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{url_host}:{port}/live.html"
    print(f"FIN Control Room: {url}")
    print(f"Runtime root: {config.runtime_root}")
    print("Mode: read-only paper/shadow monitoring; exchange submission unavailable")
    if args.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping FIN Control Room")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
