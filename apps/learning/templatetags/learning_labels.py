from datetime import timedelta

from django import template
from django.utils import formats, timezone

register = template.Library()

CATEGORY_LABELS = {
    "spelling": "Ortografie",
    "verb_tense": "Timpul verbului",
    "verb_form": "Forma verbului",
    "article": "Articol",
    "preposition": "Prepoziție",
    "word_order": "Ordinea cuvintelor",
    "subject_verb_agreement": "Acord subiect–verb",
    "conditional": "Condițional",
    "collocation": "Colocație",
    "punctuation": "Punctuație",
    "vocabulary": "Vocabular",
    "british_english": "Engleză britanică",
    "romanian_transfer": "Influența limbii române",
    "other": "Altele",
}
REQUEST_TYPE_LABELS = {"correction": "Corectură", "translation": "Traducere"}


@register.filter
def category_label(value):
    return CATEGORY_LABELS.get(str(value), str(value).replace("_", " ").capitalize())


def romanian_count(count, one, many):
    """Romanian count phrase: 1 greșeală, 2–19 greșeli, 20 de greșeli."""
    count = int(count)
    if count == 1:
        return f"1 {one}"
    of = "de " if count >= 20 and (count % 100 == 0 or count % 100 >= 20) else ""
    return f"{count} {of}{many}"


@register.filter
def recorded_mistakes(count):
    return romanian_count(count, "greșeală înregistrată", "greșeli înregistrate")


@register.filter
def history_day_label(day):
    today = timezone.localdate()
    if day == today:
        return "Azi"
    if day == today - timedelta(days=1):
        return "Ieri"
    return formats.date_format(day, "j F Y")


@register.filter
def history_day_summary(day):
    parts = [romanian_count(day[key], one, many) for key, one, many in
             (("corrections", "corectură", "corecturi"), ("translations", "traducere", "traduceri")) if day[key]]
    return " · ".join(parts)


STATUS_LABELS = {"new": "De exersat", "recurring": "Se repetă", "improving": "Se îmbunătățește",
                 "mastered": "Rezolvat", "resurfaced": "A revenit"}


@register.filter
def pattern_status_label(value):
    return STATUS_LABELS.get(str(value), str(value))


@register.filter
def pattern_name(key):
    from apps.learning.taxonomy import pattern_label
    return pattern_label(str(key))


@register.filter
def hint_for(hints, key):
    return hints.get(key) if isinstance(hints, dict) else None


@register.filter
def times_phrase(count):
    """"o dată", "de 4 ori", "de 20 de ori"."""
    count = int(count)
    return "o dată" if count == 1 else f"de {romanian_count(count, 'dată', 'ori')}"


@register.filter
def request_type_label(value):
    return REQUEST_TYPE_LABELS.get(str(value), str(value))
