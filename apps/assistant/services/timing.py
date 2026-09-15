"""Server-side durations of one request, in whole milliseconds. Names and numbers only, never content."""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

MAX_MS = 600_000  # Ten minutes: anything larger is not a real duration of one request.
_NAME = re.compile(r"^[a-z][a-z_]{0,30}$")
_timings: ContextVar[dict[str, float] | None] = ContextVar("request_timings", default=None)


@contextmanager
def collect_timings():
    """Collects the durations recorded inside the block (Server-Timing header, usage ledger)."""
    timings: dict[str, float] = {}
    token = _timings.set(timings)
    try:
        yield timings
    finally:
        _timings.reset(token)


def record_timing(name: str, milliseconds: float) -> None:
    timings = _timings.get()
    if timings is not None:
        timings[name] = timings.get(name, 0.0) + max(0.0, milliseconds)


@contextmanager
def timed(name: str):
    started = perf_counter()
    try:
        yield
    finally:
        record_timing(name, (perf_counter() - started) * 1000)


def since(started: float) -> int:
    """Whole milliseconds since a perf_counter() reading."""
    return bounded_ms((perf_counter() - started) * 1000)


def bounded_ms(value) -> int | None:
    """A duration as a whole number of milliseconds in 0..MAX_MS, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    rounded = round(value)
    return rounded if 0 <= rounded <= MAX_MS else None


def server_timing_header(timings: dict[str, float]) -> str:
    """A Server-Timing header value such as `db;dur=12, provider;dur=840`: lets developers see where time went."""
    parts = []
    for name, value in timings.items():
        milliseconds = bounded_ms(value)
        if _NAME.match(name) and milliseconds is not None:
            parts.append(f"{name};dur={milliseconds}")
    return ", ".join(parts)
