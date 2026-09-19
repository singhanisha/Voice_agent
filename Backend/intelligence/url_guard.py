"""SSRF protection helpers for website intelligence jobs."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class URLSafetyError(ValueError):
    """Raised when a URL is unsafe to crawl."""


@dataclass(frozen=True)
class SafeURL:
    url: str
    normalized_url: str
    domain: str
    scheme: str


def validate_public_http_url(
    url: str,
    *,
    resolve_dns: bool = True,
    allowed_domains: set[str] | None = None,
) -> SafeURL:
    raw = (url or "").strip()
    if not raw:
        raise URLSafetyError("url is required")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise URLSafetyError("only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise URLSafetyError("embedded credentials are not allowed")
    if parsed.port and parsed.port not in {80, 443}:
        raise URLSafetyError("non-standard ports are not allowed")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLSafetyError("url host is required")
    if allowed_domains and host not in allowed_domains and not any(host.endswith(f".{d}") for d in allowed_domains):
        raise URLSafetyError("domain is not allowed")

    _reject_unsafe_host_literal(host)
    if resolve_dns:
        _reject_unsafe_dns(host)

    normalized = parsed._replace(fragment="", netloc=host if not parsed.port else f"{host}:{parsed.port}").geturl()
    return SafeURL(url=raw, normalized_url=normalized, domain=host, scheme=parsed.scheme)


def _reject_unsafe_host_literal(host: str) -> None:
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
        raise URLSafetyError("local hostnames are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    _reject_unsafe_ip(ip)


def _reject_unsafe_dns(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise URLSafetyError(f"host could not be resolved: {host}") from exc

    for info in infos:
        ip_text = info[4][0]
        try:
            _reject_unsafe_ip(ipaddress.ip_address(ip_text))
        except ValueError as exc:
            raise URLSafetyError("resolved address is invalid") from exc


def _reject_unsafe_ip(ip: ipaddress._BaseAddress) -> None:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"):
            return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise URLSafetyError("private or non-public IPs are not allowed")
    if str(ip) == "169.254.169.254":
        raise URLSafetyError("cloud metadata IP is not allowed")

