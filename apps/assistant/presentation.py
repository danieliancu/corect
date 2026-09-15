"""What a result shows, for fresh results and for history saved at any time (including before "Vreau să sune natural!").

Stored rows keep their original JSON: English results have corrected_text, results written in British English from
another language have translated_text, and some older rows were translated from English into Romanian.
"""
from apps.assistant.languages import CORRECTION, TARGET, TRANSLATION, source_label

ERRORS, UNNATURAL, NATURAL = "errors", "unnatural", "natural"
TRANSLATED = "translated"
LEGACY_TO_ROMANIAN = "legacy_to_romanian"  # History only: English translated into Romanian by the old Translate button.


def result_outcome(kind: str, result) -> str:
    """errors | unnatural | natural (English), translated (into British English), legacy_to_romanian, or ""."""
    if not isinstance(result, dict) or not result:
        return ""
    if kind == TRANSLATION or "translated_text" in result:
        if result.get("target_language") not in ("", None, TARGET.legacy_code, TARGET.code):
            return LEGACY_TO_ROMANIAN
        return TRANSLATED
    if kind == CORRECTION or "corrected_text" in result:
        if result.get("has_errors"):
            return ERRORS
        return UNNATURAL if str(result.get("native_text") or "").strip() else NATURAL
    return ""


def history_kind(entry) -> str:
    """english | into_english | legacy_to_romanian, from a saved AssistantRequest."""
    if entry.request_type == CORRECTION:
        return "english"
    if result_outcome(entry.request_type, entry.result_data) == LEGACY_TO_ROMANIAN:
        return LEGACY_TO_ROMANIAN
    return "into_english"


def history_label(entry) -> str:
    kind = history_kind(entry)
    if kind == "english":
        return "Engleză"
    if kind == LEGACY_TO_ROMANIAN:
        return "Engleză → română"
    source = source_label(entry.detected_language)
    return f"{source.capitalize()} → engleză" if source else "În engleză"


def history_group(entry) -> tuple[str, str]:
    """The (singular, plural) phrase that counts this entry in a history day summary."""
    kind = history_kind(entry)
    if kind == "english":
        return "text în engleză", "texte în engleză"
    if kind == LEGACY_TO_ROMANIAN:
        return "text în română", "texte în română"
    source = source_label(entry.detected_language)
    return (f"text din {source}", f"texte din {source}") if source else ("text în engleză", "texte în engleză")
