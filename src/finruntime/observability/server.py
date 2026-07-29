from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
import struct
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
from .strategy_hub import StrategyHub, read_consensus_snapshot

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WEBSOCKET_HEARTBEAT_SECONDS = 15.0


def _websocket_frame(payload: bytes, *, opcode: int = 0x1) -> bytes:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length <= 125:
        header.append(length)
    elif length <= 65_535:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + payload


@dataclass(frozen=True, slots=True)
class ControlRoomConfig:
    frontend_root: Path
    dashboard_path: Path
    runtime_root: Path
    paper_snapshot_path: Path | None = None
    consensus_snapshot_path: Path | None = None
    dyn_snapshot_path: Path | None = None
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


def _read_paper_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "mode": "paper",
            "health": "starting",
            "available": False,
            "age_seconds": None,
            "markets": [],
            "candles": [],
            "events": [],
            "scan": {"errors": ["paper snapshot is not available yet"]},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported paper snapshot schema")
    if value.get("mode") != "paper":
        raise ValueError("paper snapshot has an unexpected mode")
    updated_at_ms = value.get("updated_at_ms")
    age_seconds = (
        max(0.0, time.time() - float(updated_at_ms) / 1000.0)
        if isinstance(updated_at_ms, (int, float))
        else None
    )
    scan = value.get("scan") if isinstance(value.get("scan"), dict) else {}
    markets = value.get("markets") if isinstance(value.get("markets"), list) else []
    candles = value.get("candles") if isinstance(value.get("candles"), list) else []
    errors = scan.get("errors") if isinstance(scan.get("errors"), list) else []
    healthy = (
        age_seconds is not None
        and age_seconds <= 20.0
        and bool(markets)
        and bool(candles)
        and not errors
    )
    result = dict(value)
    result.update(
        {
            "available": True,
            "health": "healthy" if healthy else "degraded",
            "age_seconds": age_seconds,
        }
    )
    return result


def build_dashboard_payload(config: ControlRoomConfig) -> dict[str, Any]:
    historical = _read_dashboard(config.dashboard_path)
    runtime = build_runtime_snapshot(
        config.runtime_root,
        stale_after_seconds=config.stale_after_seconds,
        incident_limit=config.incident_limit,
    )
    result = dict(historical)
    result["runtime"] = runtime
    paper_path = config.paper_snapshot_path or (
        config.runtime_root / "funding_router_snapshot.json"
    )
    result["paper"] = _read_paper_snapshot(paper_path)
    result["environment"] = dict(historical.get("environment") or {})
    result["environment"].update(
        {
            "control_plane": "read-only",
            "runtime_status": runtime["status"],
            "runtime_strategy_count": runtime["aggregate"]["strategy_count"],
            "runtime_observation_count": runtime["aggregate"]["observation_count"],
            "runtime_incident_count": runtime["aggregate"]["critical_incidents"]
            + runtime["aggregate"]["warning_incidents"],
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
        self.strategy_hub = StrategyHub(dyn_snapshot_path=config.dyn_snapshot_path)
        self.started_monotonic = time.monotonic()
        super().__init__(address, ControlRoomHandler)


class ControlRoomHandler(BaseHTTPRequestHandler):
    server: ControlRoomHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(
            f"[{self.log_date_time_string()}] {self.client_address[0]} {format % args}"
        )

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
        )

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
        payload = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        self._send_bytes(
            payload,
            content_type="application/json; charset=utf-8",
            status=status,
            etag=hashlib.sha256(payload).hexdigest(),
            head_only=head_only,
        )

    def _runtime(self) -> dict[str, object]:
        return build_runtime_snapshot(
            self.server.config.runtime_root,
            stale_after_seconds=self.server.config.stale_after_seconds,
            incident_limit=self.server.config.incident_limit,
        )

    def _dashboard(self) -> dict[str, Any]:
        return build_dashboard_payload(self.server.config)

    def _paper(self) -> dict[str, Any]:
        path = self.server.config.paper_snapshot_path or (
            self.server.config.runtime_root / "funding_router_snapshot.json"
        )
        return _read_paper_snapshot(path)

    def _consensus(self) -> dict[str, Any]:
        path = self.server.config.consensus_snapshot_path or (
            self.server.config.runtime_root / "consensus_paper_snapshot.json"
        )
        return read_consensus_snapshot(path)

    def _strategies(self) -> dict[str, Any]:
        return self.server.strategy_hub.snapshot(
            funding=self._paper(),
            runtime=self._runtime(),
            consensus=self._consensus(),
        )

    def _realtime_snapshot(self) -> dict[str, Any]:
        funding = self._paper()
        runtime = self._runtime()
        consensus = self._consensus()
        strategies = self.server.strategy_hub.snapshot(
            funding=funding,
            runtime=runtime,
            consensus=consensus,
        )
        return {
            "schema_version": 1,
            "type": "snapshot",
            "generated_at_ms": int(time.time() * 1000),
            "paper": funding,
            "strategies": strategies,
        }

    @staticmethod
    def _realtime_digest(payload: dict[str, Any]) -> str:
        paper = dict(payload["paper"])
        paper.pop("age_seconds", None)
        strategies = dict(payload["strategies"])
        strategies.pop("generated_at_ms", None)
        return snapshot_digest(
            {
                "paper": paper,
                "strategies": strategies,
            }
        )

    def _health(self) -> dict[str, object]:
        runtime = self._runtime()
        paper = self._paper()
        consensus = self._consensus()
        consensus_updated_at_ms = consensus.get("market_data_at_ms")
        consensus_age_seconds = (
            max(0.0, time.time() - float(consensus_updated_at_ms) / 1000.0)
            if isinstance(consensus_updated_at_ms, (int, float))
            else None
        )
        strategy_snapshot = self.server.strategy_hub.snapshot(
            funding=paper,
            runtime=runtime,
            consensus=consensus,
        )
        strategy_health = {
            item["id"]: {
                "status": item["status"],
                "status_label": item["status_label"],
                "updated_at_ms": item.get("updated_at_ms"),
                "error": item.get("detail", {}).get("upstream_error"),
            }
            for item in strategy_snapshot["strategies"]
        }
        aggregate_status = (
            "healthy"
            if runtime["status"] == "healthy"
            and all(
                item["status"] in {"running", "healthy"}
                for item in strategy_snapshot["strategies"]
            )
            else "degraded"
        )
        return {
            "service": "fin-control-room",
            "status": aggregate_status,
            "uptime_seconds": round(
                time.monotonic() - self.server.started_monotonic, 3
            ),
            "runtime_root_exists": runtime["runtime_root_exists"],
            "aggregate": runtime["aggregate"],
            "read_only": True,
            "paper": {
                "health": paper.get("health"),
                "available": paper.get("available"),
                "age_seconds": paper.get("age_seconds"),
            },
            "consensus_paper": {
                "health": consensus.get("health"),
                "available": consensus_updated_at_ms is not None,
                "age_seconds": consensus_age_seconds,
                "open_positions": len(consensus.get("paper", {}).get("positions", [])),
            },
            "strategies": strategy_health,
            "realtime": {
                "transport": "websocket",
                "path": "/api/v1/ws",
                "heartbeat_seconds": WEBSOCKET_HEARTBEAT_SECONDS,
            },
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
            if path == "/api/v1/paper":
                self._send_json(self._paper(), head_only=head_only)
                return True
            if path == "/api/v1/strategies":
                self._send_json(self._strategies(), head_only=head_only)
                return True
            if path == "/api/v1/incidents":
                runtime = self._runtime()
                self._send_json(
                    {
                        "schema_version": 1,
                        "generated_at_utc": runtime["generated_at_utc"],
                        "status": runtime["status"],
                        "incidents": runtime["incidents"],
                    },
                    head_only=head_only,
                )
                return True
            if path == "/api/v1/scheduler":
                runtime = self._runtime()
                self._send_json(
                    {
                        "schema_version": 1,
                        "generated_at_utc": runtime["generated_at_utc"],
                        "scheduler": runtime.get("scheduler", {}),
                        "exchange_submission_available": False,
                    },
                    head_only=head_only,
                )
                return True
            if path == "/api/v1/health":
                self._send_json(self._health(), head_only=head_only)
                return True
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._send_json(
                {
                    "error": "control_room_snapshot_failed",
                    "detail": str(error),
                    "live_ready": False,
                    "exchange_submission_available": False,
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                head_only=head_only,
            )
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
                    data = json.dumps(
                        {
                            "digest": digest,
                            "generated_at_utc": payload.get("generated_at_utc"),
                            "runtime_status": payload.get("runtime", {}).get("status"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode())
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

    def _websocket_request_is_valid(self) -> bool:
        upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
        connection_tokens = {
            token.strip().lower()
            for token in self.headers.get("Connection", "").split(",")
        }
        key = self.headers.get("Sec-WebSocket-Key", "")
        try:
            decoded_key = base64.b64decode(key, validate=True)
        except (binascii.Error, ValueError):
            decoded_key = b""
        return (
            upgrade
            and "upgrade" in connection_tokens
            and self.headers.get("Sec-WebSocket-Version") == "13"
            and len(decoded_key) == 16
        )

    def _serve_websocket(self) -> None:
        if not self._websocket_request_is_valid():
            self._send_json(
                {"error": "websocket_upgrade_required"},
                status=HTTPStatus.UPGRADE_REQUIRED,
            )
            return

        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and urlparse(origin).netloc != host:
            self._send_json(
                {"error": "websocket_origin_rejected"},
                status=HTTPStatus.FORBIDDEN,
            )
            return

        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(
            hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode()).digest()
        ).decode()
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        last_digest: str | None = None
        last_heartbeat = time.monotonic()
        try:
            while True:
                payload = self._realtime_snapshot()
                digest = self._realtime_digest(payload)
                now = time.monotonic()
                if digest != last_digest:
                    message = payload
                    last_digest = digest
                elif now - last_heartbeat >= WEBSOCKET_HEARTBEAT_SECONDS:
                    message = {
                        "schema_version": 1,
                        "type": "heartbeat",
                        "sent_at_ms": int(time.time() * 1000),
                    }
                else:
                    time.sleep(self.server.config.poll_seconds)
                    continue
                encoded = json.dumps(
                    message,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.connection.sendall(_websocket_frame(encoded))
                last_heartbeat = now
                time.sleep(self.server.config.poll_seconds)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.close_connection = True

    def _static_path(self, request_path: str) -> Path | None:
        relative = unquote(request_path).lstrip("/") or "live.html"
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
            self._send_json(
                {"error": "not_found", "path": path},
                status=HTTPStatus.NOT_FOUND,
                head_only=head_only,
            )
            return
        payload = file_path.read_bytes()
        content_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self._send_bytes(
            payload,
            content_type=content_type,
            cache_control="no-cache",
            etag=hashlib.sha256(payload).hexdigest(),
            head_only=head_only,
        )

    def _dispatch_get(self, *, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/ws":
            if head_only:
                self._send_json(
                    {"error": "head_not_supported"},
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                    head_only=True,
                )
            else:
                self._serve_websocket()
            return
        if parsed.path == "/api/v1/events":
            if head_only:
                self._send_json(
                    {"error": "head_not_supported"},
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                    head_only=True,
                )
            else:
                try:
                    self._serve_events(parse_qs(parsed.query))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self.log_error("event stream failed: %s", error)
            return
        if self._serve_api(parsed.path, head_only=head_only):
            return
        self._serve_static(parsed.path, head_only=head_only)

    def do_GET(self) -> None:
        self._dispatch_get()

    def do_HEAD(self) -> None:
        self._dispatch_get(head_only=True)

    def do_POST(self) -> None:
        self._send_json(
            {
                "error": "method_not_allowed",
                "detail": "FIN Control Room is read-only and exposes no order or mutation endpoints.",
                "exchange_submission_available": False,
            },
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )


def create_server(
    *,
    host: str,
    port: int,
    frontend_root: str | Path,
    dashboard_path: str | Path,
    runtime_root: str | Path,
    paper_snapshot_path: str | Path | None = None,
    consensus_snapshot_path: str | Path | None = None,
    dyn_snapshot_path: str | Path | None = None,
    stale_after_seconds: int = 172_800,
    incident_limit: int = 100,
    poll_seconds: float = 2.0,
) -> ControlRoomHTTPServer:
    config = ControlRoomConfig(
        frontend_root=Path(frontend_root).expanduser().resolve(),
        dashboard_path=Path(dashboard_path).expanduser().resolve(),
        runtime_root=Path(runtime_root).expanduser().resolve(),
        paper_snapshot_path=(
            Path(paper_snapshot_path).expanduser().resolve()
            if paper_snapshot_path is not None
            else None
        ),
        consensus_snapshot_path=(
            Path(consensus_snapshot_path).expanduser().resolve()
            if consensus_snapshot_path is not None
            else None
        ),
        dyn_snapshot_path=(
            Path(dyn_snapshot_path).expanduser().resolve()
            if dyn_snapshot_path is not None
            else None
        ),
        stale_after_seconds=stale_after_seconds,
        incident_limit=incident_limit,
        poll_seconds=poll_seconds,
    )
    return ControlRoomHTTPServer((host, port), config)


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the read-only FIN paper/shadow control room."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend-root", default="frontend")
    parser.add_argument("--dashboard", default="frontend/data/dashboard.json")
    parser.add_argument(
        "--runtime-root", default=os.environ.get("FIN_RUNTIME_ROOT", "runtime")
    )
    parser.add_argument(
        "--consensus-snapshot",
        default=os.environ.get("FIN_CONSENSUS_SNAPSHOT"),
    )
    parser.add_argument(
        "--paper-snapshot",
        default=os.environ.get("FIN_FUNDING_SNAPSHOT"),
    )
    parser.add_argument(
        "--dyn-snapshot",
        default=os.environ.get("FIN_DYN_SNAPSHOT"),
    )
    parser.add_argument("--stale-after-seconds", type=int, default=172_800)
    parser.add_argument("--incident-limit", type=int, default=100)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args(argv)

    if not _is_loopback(args.host) and not args.allow_remote:
        parser.error(
            "non-loopback binding requires --allow-remote; the server has no authentication layer"
        )

    config = ControlRoomConfig(
        frontend_root=Path(args.frontend_root).expanduser().resolve(),
        dashboard_path=Path(args.dashboard).expanduser().resolve(),
        runtime_root=Path(args.runtime_root).expanduser().resolve(),
        paper_snapshot_path=(
            Path(args.paper_snapshot).expanduser().resolve()
            if args.paper_snapshot
            else None
        ),
        consensus_snapshot_path=(
            Path(args.consensus_snapshot).expanduser().resolve()
            if args.consensus_snapshot
            else None
        ),
        dyn_snapshot_path=(
            Path(args.dyn_snapshot).expanduser().resolve()
            if args.dyn_snapshot
            else None
        ),
        stale_after_seconds=args.stale_after_seconds,
        incident_limit=args.incident_limit,
        poll_seconds=args.poll_seconds,
    )
    config.validate()
    if args.snapshot:
        print(
            json.dumps(
                build_dashboard_payload(config),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
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
