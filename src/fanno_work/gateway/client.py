from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any
import uuid


class GatewayClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(
            f"{code}: {message}"
        )
        self.code = code
        self.message = message


class GatewayClient:
    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout_seconds: float = 120,
        max_response_bytes: int = (
            32 * 1024 * 1024
        ),
    ) -> None:
        self.socket_path = str(socket_path)
        self.timeout_seconds = (
            timeout_seconds
        )
        self.max_response_bytes = (
            max_response_bytes
        )

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        request = {
            "request_id": uuid.uuid4().hex,
            "method": method,
            "params": params or {},
        }

        payload = (
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

        with socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        ) as connection:
            connection.settimeout(
                self.timeout_seconds
            )
            connection.connect(
                self.socket_path
            )
            connection.sendall(payload)
            connection.shutdown(
                socket.SHUT_WR
            )

            chunks: list[bytes] = []
            total = 0

            while True:
                chunk = connection.recv(65536)

                if not chunk:
                    break

                chunks.append(chunk)
                total += len(chunk)

                if (
                    total
                    > self.max_response_bytes
                ):
                    raise GatewayClientError(
                        "response_too_large",
                        (
                            "gateway response "
                            "exceeds limit"
                        ),
                    )

        raw = b"".join(chunks)

        try:
            response = json.loads(
                raw.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise GatewayClientError(
                "invalid_response",
                str(exc),
            ) from exc

        if not isinstance(response, dict):
            raise GatewayClientError(
                "invalid_response",
                "response must be an object",
            )

        if response.get("ok") is not True:
            error = response.get(
                "error",
                {},
            )

            if not isinstance(error, dict):
                error = {}

            raise GatewayClientError(
                str(
                    error.get(
                        "code",
                        "gateway_error",
                    )
                ),
                str(
                    error.get(
                        "message",
                        "",
                    )
                ),
            )

        return response.get("result")