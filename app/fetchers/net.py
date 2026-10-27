"""Outbound HTTP for untrusted URLs (feeds, linked articles, saved pages).

Any user can make the server fetch a URL, so connections to loopback, private, link-local
and other non-public addresses are refused. The check runs at connect time, on the address
that is actually dialled, so it also covers redirects and DNS rebinding.
"""

import asyncio
import ipaddress
import socket

import httpcore
import httpx

from app.config import get_settings


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4 tunnelled in IPv6 (::ffff:127.0.0.1, 6to4, Teredo) is judged by the IPv4 address
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None:
            ip = embedded
    return ip.is_global and not ip.is_multicast


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend):
        self._inner = inner

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout
            )
        except (OSError, TimeoutError) as e:
            raise httpcore.ConnectError(f"cannot resolve {host}: {e}") from e
        ips = list(dict.fromkeys(info[4][0] for info in infos))
        if not ips or not all(is_public_ip(ip) for ip in ips):
            raise httpcore.ConnectError(f"refusing to connect to {host}: not a public address")
        error: Exception | None = None
        for ip in ips:  # dial the vetted address, never the name again
            try:
                return await self._inner.connect_tcp(ip, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as e:
                error = e
        raise error

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pool._network_backend = _PublicOnlyBackend(self._pool._network_backend)


def fetch_client(**kwargs) -> httpx.AsyncClient:
    """An httpx client for URLs that came from users or feeds.

    FETCH_ALLOW_PRIVATE_ADDRESSES=true turns the guard off, for feeds on your own network.
    A guarded client doesn't use HTTP(S)_PROXY from the environment.
    """
    if get_settings().fetch_allow_private_addresses:
        return httpx.AsyncClient(**kwargs)
    return httpx.AsyncClient(transport=PublicOnlyTransport(), **kwargs)
