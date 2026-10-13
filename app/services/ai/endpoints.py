"""Checks for model endpoint URLs that account owners type in (Azure OpenAI and custom OpenAI-compatible).

A custom URL must not let someone point the worker at addresses inside AWS, such as the Lambda runtime API on
localhost or the 169.254.x.x link-local range, so only public HTTPS hosts are accepted. The check runs when the
setting is saved and again before every call, because DNS can change in between.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]


class UnsafeEndpoint(ValueError):
    pass


def resolve_host(host: str) -> list[str]:
    return list({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def check_endpoint(url: str, *, resolver: Resolver = resolve_host) -> str:
    """The URL without a trailing slash, or UnsafeEndpoint with a message an owner can act on."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeEndpoint("The endpoint must be an https:// URL.")
    if parts.username or parts.password:
        raise UnsafeEndpoint("Put the API key in the key field, not in the URL.")
    try:
        addresses = resolver(parts.hostname)
    except OSError as exc:
        raise UnsafeEndpoint(f"{parts.hostname} could not be found.") from exc
    if not addresses:
        raise UnsafeEndpoint(f"{parts.hostname} could not be found.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeEndpoint("The endpoint must be reachable on the public internet.")
    return url.strip().rstrip("/")
