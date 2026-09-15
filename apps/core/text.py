"""Small Romanian text helpers shared by the interface."""


def romanian_count(count, one, many):
    """Romanian count phrase: 1 greșeală, 2–19 greșeli, 20 de greșeli."""
    count = int(count)
    if count == 1:
        return f"1 {one}"
    of = "de " if count >= 20 and (count % 100 == 0 or count % 100 >= 20) else ""
    return f"{count} {of}{many}"
