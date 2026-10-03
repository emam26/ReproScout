"""SSRF-safe public URL validation and bounded HTTP asset retrieval."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class PublicUrlError(ValueError):
    """Raised when a repository-provided URL is unsafe or unavailable."""


@dataclass(frozen=True, slots=True)
class SafeDownloadLimits:
    """Bounds applied to every public asset retrieval."""

    timeout_seconds: float = 15.0
    max_bytes: int = 50_000_000
    max_redirects: int = 3
    chunk_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if (
            self.timeout_seconds <= 0
            or self.timeout_seconds > 300
            or self.max_bytes < 1
            or self.max_bytes > 10_000_000_000
            or self.max_redirects < 0
            or self.max_redirects > 10
        ):
            raise ValueError("Safe download limits must be positive and bounded.")
        if self.chunk_bytes < 1 or self.chunk_bytes > 1_048_576:
            raise ValueError("Download chunks must be between 1 byte and 1 MiB.")


@dataclass(frozen=True, slots=True)
class DownloadedAsset:
    """Bounded asset bytes and the final validated public URL."""

    requested_url: str
    final_url: str
    content: bytes
    redirects: int
    content_type: str | None


Resolver = Callable[[str, int], list[str]]


def _default_resolver(host: str, port: int) -> list[str]:
    return list(
        {
            str(info[4][0])
            for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    )


def _blocked_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_public_url(
    value: str,
    *,
    resolve: bool = False,
    resolver: Resolver | None = None,
    allow_query: bool = False,
) -> str:
    """Validate HTTPS and reject credential, local, and internal destinations.

    Callers that are about to connect should use ``resolve=True``. Every
    redirect is validated with the same function before the next connection.
    """

    if not isinstance(value, str) or not value or len(value) > 2_000:
        raise PublicUrlError("Public URL is empty or too long.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PublicUrlError("Public URL is malformed.") from exc
    host = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or (parsed.query and not allow_query)
    ):
        raise PublicUrlError(
            "Only credential-free HTTPS URLs to public destinations are allowed."
        )
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".localhost", ".internal")
    ):
        raise PublicUrlError("Local and internal URL destinations are not allowed.")
    try:
        if _blocked_address(host):
            raise PublicUrlError(
                "Private and special-use URL destinations are not allowed."
            )
        addresses = (
            [host]
            if not resolve
            else (resolver or _default_resolver)(host, port or 443)
        )
    except PublicUrlError:
        raise
    except (OSError, socket.gaierror) as exc:
        raise PublicUrlError("URL hostname could not be resolved safely.") from exc
    if not addresses:
        raise PublicUrlError("URL hostname resolved to no addresses.")
    if any(_blocked_address(address) for address in addresses):
        raise PublicUrlError(
            "URL hostname resolves to a private or special-use address."
        )
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def download_public_url(
    url: str,
    *,
    limits: SafeDownloadLimits | None = None,
    resolver: Resolver | None = None,
    opener=None,  # type: ignore[no-untyped-def]
) -> DownloadedAsset:
    """Download bounded bytes while revalidating every redirect destination."""

    policy = limits or SafeDownloadLimits()
    requested = validate_public_url(url, resolve=True, resolver=resolver)
    http = opener or build_opener(_NoRedirectHandler())
    current = requested
    redirects = 0
    while True:
        current = validate_public_url(current, resolve=True, resolver=resolver)
        request = Request(current, headers={"User-Agent": "ReproScout/0.1"})
        response = None
        try:
            response = http.open(request, timeout=policy.timeout_seconds)
        except HTTPError as exc:
            response = exc
        except (OSError, URLError, TimeoutError) as exc:
            raise PublicUrlError("Public asset request failed.") from exc
        status = response.getcode()
        if status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if redirects >= policy.max_redirects or not location:
                raise PublicUrlError("Public asset redirect limit was exceeded.")
            redirects += 1
            current = urljoin(current, location)
            continue
        if status < 200 or status >= 300:
            response.close()
            raise PublicUrlError("Public asset server returned an unsuccessful status.")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                response.close()
                raise PublicUrlError(
                    "Public asset reported an invalid byte length."
                ) from exc
            if declared_length > policy.max_bytes:
                response.close()
                raise PublicUrlError("Public asset exceeds the byte limit.")
        content = bytearray()
        try:
            while True:
                chunk = response.read(policy.chunk_bytes)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > policy.max_bytes:
                    raise PublicUrlError("Public asset exceeds the byte limit.")
        finally:
            response.close()
        return DownloadedAsset(
            requested_url=requested,
            final_url=current,
            content=bytes(content),
            redirects=redirects,
            content_type=response.headers.get("Content-Type"),
        )
