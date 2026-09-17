"""The visitor's real IP address, safe behind reverse proxies.

Forwarding headers are written by whoever sends the request, so they are trusted only when the connection itself comes
from a proxy listed in TRUSTED_PROXY_CIDRS, and only the one header named by CLIENT_IP_HEADER is read:

- "none" (default): REMOTE_ADDR only; every forwarding header is ignored.
- "x-forwarded-for": nginx or a load balancer that appends the address it saw. The list is read right to left, skipping
  trusted proxies; the first untrusted address is the visitor. Anything a client put further left is ignored.
- "x-real-ip" / "cf-connecting-ip": a single address set by the trusted proxy (nginx real_ip, Cloudflare).

The result is used for rate limits only (apps/assistant/services/limits.py) and is never stored.
"""
import ipaddress
from functools import lru_cache

from django.conf import settings

HEADERS = {"x-forwarded-for": "HTTP_X_FORWARDED_FOR", "x-real-ip": "HTTP_X_REAL_IP",
           "cf-connecting-ip": "HTTP_CF_CONNECTING_IP"}
UNKNOWN = "unknown"
MAX_FORWARDED_HOPS = 20


@lru_cache(maxsize=8)
def trusted_networks(cidrs: tuple) -> tuple:
    return tuple(ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)


def parse_ip(value):
    """An ip_address from a header or REMOTE_ADDR value, tolerating ports and IPv4-mapped IPv6; None when invalid."""
    value = (value or "").strip().strip('"')
    if value.startswith("[") and "]" in value:  # "[2001:db8::1]:443"
        value = value[1:value.index("]")]
    elif value.count(":") == 1:  # "203.0.113.5:443"
        value = value.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_trusted(address, networks) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def client_address(request):
    """The visitor's address as an ip_address object, or None when not even REMOTE_ADDR is usable."""
    remote = parse_ip(request.META.get("REMOTE_ADDR"))
    header = getattr(settings, "CLIENT_IP_HEADER", "none")
    networks = trusted_networks(tuple(getattr(settings, "TRUSTED_PROXY_CIDRS", ())))
    if remote is None or header not in HEADERS or not is_trusted(remote, networks):
        return remote
    raw = request.META.get(HEADERS[header], "")
    if header != "x-forwarded-for":
        return parse_ip(raw) or remote
    client = remote
    for hop in reversed(raw.split(",")[-MAX_FORWARDED_HOPS:]):
        address = parse_ip(hop)
        if address is None:
            break  # A malformed entry ends the chain: keep the last address a trusted proxy vouched for.
        client = address
        if not is_trusted(address, networks):
            break
    return client


def client_ip(request) -> str:
    address = client_address(request)
    return str(address) if address is not None else UNKNOWN


def rate_limit_identity(request) -> str:
    """What an anonymous rate limit counts: the IPv4 address, or the /64 network for IPv6, because one IPv6 connection
    usually controls a whole /64 and could otherwise pick a new address for every request."""
    address = client_address(request)
    if address is None:
        return UNKNOWN
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)
