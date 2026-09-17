"""Validated whole-number settings read from the environment, used by config/settings.py.

Plain functions over a mapping, so impossible values can be tested without reloading the settings module.
"""
import os

from django.core.exceptions import ImproperlyConfigured

TIERS = ("anonymous", "free", "pro")


def int_setting(name, default, low, high, env=None):
    env = os.environ if env is None else env
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        value = low - 1
    if not low <= value <= high:
        raise ImproperlyConfigured(f"{name} must be a whole number from {low} to {high}.")
    return value


def naturalize_limits(env=None):
    """The plan quota per tier: at least one use a day, never decreasing from anonymous to free to pro."""
    limits = {"anonymous": int_setting("NATURALIZE_ANONYMOUS_LIMIT_DAY", 5, 1, 100000, env),
              "free": int_setting("NATURALIZE_FREE_LIMIT_DAY", 20, 1, 100000, env),
              "pro": int_setting("NATURALIZE_PRO_LIMIT_DAY", 200, 1, 100000, env)}
    if not limits["anonymous"] <= limits["free"] <= limits["pro"]:
        raise ImproperlyConfigured("NATURALIZE_ANONYMOUS_LIMIT_DAY <= NATURALIZE_FREE_LIMIT_DAY <= NATURALIZE_PRO_LIMIT_DAY "
                                   "must hold.")
    return limits


def tier_limits(name, defaults, env=None):
    """Three whole numbers for anonymous, free and pro, e.g. "20,80,400"; each at least 1 and never decreasing."""
    env = os.environ if env is None else env
    raw = env.get(name)
    try:
        values = tuple(int(part) for part in raw.split(",")) if raw is not None else tuple(defaults)
    except ValueError:
        values = ()
    if len(values) != 3 or min(values) < 1 or list(values) != sorted(values):
        raise ImproperlyConfigured(f"{name} must be three whole numbers of at least 1 for anonymous, free and pro, not "
                                   f"decreasing (for example {','.join(map(str, defaults))}).")
    return dict(zip(TIERS, values))


CLIENT_IP_HEADERS = ("none", "x-forwarded-for", "x-real-ip", "cf-connecting-ip")


def client_ip_settings(env=None):
    """CLIENT_IP_HEADER and TRUSTED_PROXY_CIDRS (apps/core/client_ip.py), refused when they would let anyone choose the
    address the rate limits count."""
    import ipaddress

    env = os.environ if env is None else env
    header = env.get("CLIENT_IP_HEADER", "none").strip().lower() or "none"
    if header not in CLIENT_IP_HEADERS:
        raise ImproperlyConfigured(f"CLIENT_IP_HEADER must be one of {', '.join(CLIENT_IP_HEADERS)}.")
    cidrs = []
    for part in env.get("TRUSTED_PROXY_CIDRS", "").split(","):
        if not part.strip():
            continue
        try:
            network = ipaddress.ip_network(part.strip(), strict=False)
        except ValueError:
            raise ImproperlyConfigured(f"TRUSTED_PROXY_CIDRS has an invalid address or network: {part.strip()!r}.") from None
        if network.prefixlen == 0:
            raise ImproperlyConfigured("TRUSTED_PROXY_CIDRS must not trust every address (0.0.0.0/0 or ::/0).")
        cidrs.append(str(network))
    if header != "none" and not cidrs:
        raise ImproperlyConfigured("CLIENT_IP_HEADER needs TRUSTED_PROXY_CIDRS: list the proxies allowed to set it.")
    return header, cidrs
