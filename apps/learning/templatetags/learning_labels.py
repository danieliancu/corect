from django import template

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


@register.filter
def recorded_mistakes(count):
    """Romanian count phrase: 1 greșeală înregistrată, 2–19 greșeli înregistrate, 20 de greșeli înregistrate."""
    count = int(count)
    if count == 1:
        return "1 greșeală înregistrată"
    of = "de " if count >= 20 and (count % 100 == 0 or count % 100 >= 20) else ""
    return f"{count} {of}greșeli înregistrate"


@register.filter
def request_type_label(value):
    return REQUEST_TYPE_LABELS.get(str(value), str(value))
