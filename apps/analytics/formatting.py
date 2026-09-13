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


def format_duration(seconds):
    """Audio duration such as "45.2 s" or "3 min 12 s"."""
    if seconds is None:
        return UNKNOWN
    seconds = Decimal(seconds)
    if seconds < 60:
        return f"{seconds.normalize():f} s" if seconds == seconds.to_integral() else f"{seconds:.1f} s"
    minutes, rest = divmod(int(seconds.to_integral()), 60)
    return f"{minutes:,} min {rest} s"


AUDIO_OPERATIONS = {"transcription": "Voice input · speech to text", "speech": "Voice output · British TTS"}


def format_audio_operation(value):
    return AUDIO_OPERATIONS.get(value, value)


def rate(part, whole):
    return part / whole if whole else None
