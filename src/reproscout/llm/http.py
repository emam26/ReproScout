"""Small injectable asynchronous HTTP transport for provider adapters."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ProviderNetworkError, ProviderTimeoutError


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Minimal HTTP response independent of a vendor SDK."""

    status_code: int
    body: str


class AsyncHTTPTransport(Protocol):
    """Injectable transport used by real adapters and offline tests."""

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> HTTPResponse: ...


class UrllibHTTPTransport:
    """Standard-library HTTPS transport executed off the event loop."""

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> HTTPResponse:
        return await asyncio.to_thread(
            self._post_json_sync,
            url,
            headers,
            payload,
            timeout_seconds,
        )

    @staticmethod
    def _post_json_sync(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> HTTPResponse:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HTTPResponse(
                    status_code=response.status,
                    body=response.read().decode("utf-8", errors="replace"),
                )
        except HTTPError as exc:
            return HTTPResponse(
                status_code=exc.code,
                body=exc.read().decode("utf-8", errors="replace"),
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError("LLM provider request timed out.") from exc
        except URLError as exc:
            raise ProviderNetworkError("LLM provider network request failed.") from exc
