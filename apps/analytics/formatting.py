from decimal import Decimal

UNKNOWN = "—"


def format_money(value):
    if value is None:
        return UNKNOWN
    value = Decimal(value)
    if value == 0:
        return "$0.00"
    if abs(value) < Decimal("0.0001"):
        return f"${value:.6f}"
    return f"${value:.4f}" if abs(value) < Decimal("0.01") else f"${value:,.2f}"


def format_number(value):
    return UNKNOWN if value is None else f"{value:,}"


def format_percent(value):
    return UNKNOWN if value is None else f"{value:.1%}"


def rate(part, whole):
    return part / whole if whole else None
