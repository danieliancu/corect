"""Plain-text highlighting; all content is escaped by Django templates."""
import re
from difflib import SequenceMatcher

from django import template

register = template.Library()


def _segments(text, changed_ranges, snippets):
    ranges = list(changed_ranges)
    # Expand actual differences to the model's explanatory snippets when they match.
    # Unchanged occurrences of repeated snippets remain unmarked.
    for snippet in snippets:
        if not snippet:
            continue
        for match in re.finditer(re.escape(snippet), text):
            if any(start < match.end() and end > match.start() for start, end in changed_ranges):
                ranges.append((match.start(), match.end()))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts = []
    position = 0
    for start, end in merged:
        if position < start:
            parts.append({"text": text[position:start], "changed": False})
        parts.append({"text": text[start:end], "changed": True})
        position = end
    if position < len(text):
        parts.append({"text": text[position:], "changed": False})
    return parts


def _trim(text, start, end):
    # A highlight never starts or ends on the space next to the changed words.
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _token_key(token):
    if token.isspace():
        return " "
    return re.sub(r"[^\w']", "", token.replace("’", "'").lower())


@register.simple_tag
def sentence_comparison(result):
    original = result.get("original_text", "")
    corrected = result.get("corrected_text", "")
    left = list(re.finditer(r"\s+|[^\s]+", original))
    right = list(re.finditer(r"\s+|[^\s]+", corrected))
    original_ranges, corrected_ranges = [], []
    left_keys, right_keys = [_token_key(m.group()) for m in left], [_token_key(m.group()) for m in right]
    matcher = SequenceMatcher(None, left_keys, right_keys, autojunk=False)
    for operation, a, b, c, d in matcher.get_opcodes():
        # Silent capitalisation, punctuation and spacing fixes are never highlighted.
        if operation == "equal" or not any(key.strip() for key in left_keys[a:b] + right_keys[c:d]):
            continue
        if a < b:
            original_ranges.append(_trim(original, left[a].start(), left[b - 1].end()))
        if c < d:
            corrected_ranges.append(_trim(corrected, right[c].start(), right[d - 1].end()))
    corrections = [item for item in result.get("corrections", []) if not item.get("is_british_english_preference")]
    return {
        "original": _segments(original, original_ranges, [item["original"] for item in corrections]),
        "corrected": _segments(corrected, corrected_ranges, [item["replacement"] for item in corrections]),
    }
