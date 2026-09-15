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
