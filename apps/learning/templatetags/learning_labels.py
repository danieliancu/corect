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
def request_type_label(value):
    return REQUEST_TYPE_LABELS.get(str(value), str(value))
