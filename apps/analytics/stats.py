"""Latency statistics over ledger durations. Numbers only."""
from math import ceil


def percentiles(values, points=(50, 95)) -> dict:
    """Nearest-rank percentiles plus count, min and max of the non-empty values; {"n": 0} when there are none."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {"n": 0}
    summary = {"n": len(ordered), "min": ordered[0], "max": ordered[-1]}
    for point in points:
        summary[f"p{point}"] = ordered[max(0, ceil(point / 100 * len(ordered)) - 1)]
    return summary
