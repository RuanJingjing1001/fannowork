from __future__ import annotations

import json
import os
from pathlib import Path
import socketserver
import threading
from typing import Any

from fanno_work.environments.base import (
    WorkflowEnvironment,
)
from fanno_work.models import (
    ActionRequest,
    to_jsonable,
)


class GatewayProtocolError(ValueError):
    pass


class _ThreadingUnixServer(
    socketserver.ThreadingMixIn,
    socketserver.UnixStreamServer,
):
    daemon_threads = True
    allow_reuse_address = True


class _GatewayHandler(
    socketserver.StreamRequestHandler,
):
    def handle(self) -> None:
        line = self.rfile.readline(
            self.server.max_request_bytes + 1
        )

        if len(line) > self.server.max_request_bytes:
            response = {
                "request_id": None,
                "ok": False,
                "error": {
                    "code": "request_too_large",
                    "message": (
                        "gateway request exceeds limit"
                    ),
                },
            }
        else:
            try:
                request = json.loads(
                    line.decode("utf-8")
                )

                if not isinstance(request, dict):
                    raise GatewayProtocolError(
                        "request must be an object"
                    )

                response = (
                    self.server.gateway.dispatch(
                        request
                    )
                )

            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                GatewayProtocolError,
            ) as exc:
                response = {
                    "request_id": None,
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": str(exc),
                    },
                }

            except Exception as exc:
                response = {
                    "request_id": None,
                    "ok": False,
                    "error": {
                        "code": "gateway_failure",
                        "message": str(exc),
                    },
                }

        payload = (
            json.dumps(
                response,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

        self.wfile.write(
            payload.encode("utf-8")
        )


class EnvironmentGateway:
    def __init__(
        self,
        environment: WorkflowEnvironment,
        *,
        role: str,
    ) -> None:
        if role not in {
            "agent",
            "control",
        }:
            raise ValueError(
                "role must be agent or control"
            )

        self.environment = environment
        self.role = role
        self._lock = threading.RLock()

    def dispatch(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = request.get(
            "request_id"
        )
        method = request.get("method")
        params = request.get(
            "params",
            {},
        )

        if (
            not isinstance(request_id, str)
            or not request_id
        ):
            raise GatewayProtocolError(
                "request_id must be a non-empty string"
            )

        if (
            not isinstance(method, str)
            or not method
        ):
            raise GatewayProtocolError(
                "method must be a non-empty string"
            )

        if not isinstance(params, dict):
            raise GatewayProtocolError(
                "params must be an object"
            )

        try:
            with self._lock:
                result = self._execute(
                    method,
                    params,
                )

            return {
                "request_id": request_id,
                "ok": True,
                "result": to_jsonable(result),
            }

        except GatewayProtocolError as exc:
            return {
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": str(exc),
                },
            }

        except Exception as exc:
            return {
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": "environment_error",
                    "message": str(exc),
                    "type": type(exc).__name__,
                },
            }

    def _execute(
        self,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        if method == "status":
            return {
                "ready": True,
                "role": self.role,
            }

        if self.role == "agent":
            return self._execute_agent(
                method,
                params,
            )

        return self._execute_control(
            method,
            params,
        )

    def _execute_agent(
        self,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        if method == "observe":
            return self.environment.observe()

        if method == "actions":
            observation = (
                self.environment.observe()
            )

            return {
                "task_id": observation.task_id,
                "state_version": (
                    observation.state_version
                ),
                "actions": (
                    observation.available_actions
                ),
            }

        if method == "call":
            name = params.get("action")
            arguments = params.get(
                "arguments",
                {},
            )

            if (
                not isinstance(name, str)
                or not name
            ):
                raise GatewayProtocolError(
                    "action must be a non-empty string"
                )

            if not isinstance(arguments, dict):
                raise GatewayProtocolError(
                    "arguments must be an object"
                )

            return self.environment.act(
                ActionRequest(
                    name=name,
                    arguments=arguments,
                )
            )

        raise GatewayProtocolError(
            f"method is not available to agent: {method}"
        )

    def _execute_control(
        self,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        if method == "snapshot":
            return self.environment.snapshot()

        if method == "reset":
            self.environment.reset()

            return {
                "reset": True,
            }

        raise GatewayProtocolError(
            f"unknown control method: {method}"
        )


class EnvironmentGatewayServer:
    def __init__(
        self,
        *,
        socket_path: str | Path,
        gateway: EnvironmentGateway,
        mode: int,
        group_id: int | None = None,
        max_request_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.socket_path = Path(
            socket_path
        )
        self.gateway = gateway
        self.mode = mode
        self.group_id = group_id
        self.max_request_bytes = (
            max_request_bytes
        )

        self.socket_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = _ThreadingUnixServer(
            str(self.socket_path),
            _GatewayHandler,
        )

        self._server.gateway = gateway
        self._server.max_request_bytes = (
            max_request_bytes
        )

        os.chmod(
            self.socket_path,
            self.mode,
        )

        if self.group_id is not None:
            os.chown(
                self.socket_path,
                -1,
                self.group_id,
            )

        self._thread: threading.Thread | None = (
            None
        )

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError(
                "gateway server is already running"
            )

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=(
                f"gateway-{self.gateway.role}"
            ),
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        self.socket_path.unlink(
            missing_ok=True
        )