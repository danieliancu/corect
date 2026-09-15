"""Contract fixtures, not evidence of live AI language quality (apps/assistant/evals measures that)."""
from apps.assistant.languages import CORRECTION, TRANSLATION
from apps.assistant.schemas import CorrectionResult, NaturalizeResult, TranslationResult
from apps.assistant.services.naturalize import Naturalized
from apps.learning.taxonomy import derive_pattern

CORRECTION_CASES = [
    ("I didn't went to work yesterday.", "I didn't go to work yesterday.", "didn't went", "didn't go", "verb_form", "După did/didn't folosim forma de bază a verbului."),
    ("I'm agree with you.", "I agree with you.", "I'm agree", "I agree", "romanian_transfer", "Agree este deja verb, deci nu folosim am."),
    ("I have 48 years.", "I'm 48 years old.", "I have 48 years", "I'm 48 years old", "romanian_transfer", "În română spunem «am 48 de ani», dar în engleză vârsta se exprimă cu to be."),
    ("She can sings very well.", "She can sing very well.", "can sings", "can sing", "verb_form", "După can folosim forma de bază a verbului."),
    ("I've lived here for five years.", "I've lived here for five years.", "", "", "other", "Textul este deja corect."),
    ("If I had more money, I would buy a new car.", "If I had more money, I would buy a new car.", "", "", "other", "Textul este deja corect."),
    ("I made a photo yesterday.", "I took a photo yesterday.", "made a photo", "took a photo", "collocation", "În engleză spunem take a photo, nu make a photo."),
]
# (text, British English) for Romanian written or spoken in different ways.
ROMANIAN_CASES = [
    ("Nu cred că ajung la muncă înainte de nouă.", "I don't think I'll get to work before nine."),  # conversational
    ("Bună ziua, vă scriu în legătură cu reparația boilerului din apartamentul meu.",
     "Hello, I'm writing about the boiler repair in my flat."),  # formal
    ("Buna, ma poti suna cand ajungi acasa?", "Hi, can you call me when you get home?"),  # no diacritics
    ("Mulţumesc, ne vedem mâine la şcoală.", "Thanks, see you at school tomorrow."),  # cedilla diacritics (ş, ţ)
    ("Mersi mult!", "Thanks a lot!"),  # short
    ("Am un meeting mâine la 10, s-ar putea să întârzii puțin.",
     "I've got a meeting at 10 tomorrow, so I might be a bit late."),  # with English words
]
TRANSLATION_CASES = [
    ("Îmi pare rău că n-am putut să ajung mai devreme.", "I'm sorry I couldn't get here earlier.", "ro", "en"),
    ("Dacă aș avea mai mulți bani, aș cumpăra mașina asta.", "If I had more money, I'd buy this car.", "ro", "en"),
    # Only in history saved before "Vreau să sune natural!": English translated into Romanian.
    ("I've been living in London for five years.", "Locuiesc în Londra de cinci ani.", "en", "ro"),
]


def correction_result(case=CORRECTION_CASES[0]):
    text, corrected, original, replacement, category, explanation = case
    return CorrectionResult(detected_language="en", original_text=text, corrected_text=corrected,
        has_errors=bool(original), overall_explanation=explanation, native_text="", native_explanation="",
        corrections=[dict(original=original, replacement=replacement, category=category,
                          pattern=derive_pattern(category, original, replacement), severity="minor",
                         explanation_ro=explanation, is_british_english_preference=False)] if original else [])


def translation_result(case=TRANSLATION_CASES[0]):
    text, translated, source, target = case
    return TranslationResult(original_text=text, translated_text=translated, source_language=source, target_language=target)


def english_raw(case=CORRECTION_CASES[0], natural="", natural_explanation=""):
    """The provider's structured output for an English case."""
    text, corrected, original, replacement, category, explanation = case
    corrections = [dict(original=original, replacement=replacement, category=category,
                        pattern=derive_pattern(category, original, replacement), severity="minor",
                        explanation_ro=explanation, is_british_english_preference=False)] if original else []
    return NaturalizeResult(source_language="en", corrected_text=corrected, has_errors=bool(original),
                            corrections=corrections, overall_explanation=explanation, natural_text=natural,
                            natural_explanation=natural_explanation)


def romanian_raw(case=ROMANIAN_CASES[0]):
    """The provider's structured output for a Romanian case."""
    return NaturalizeResult(source_language="ro", corrected_text="", has_errors=False, corrections=[],
                            overall_explanation="", natural_text=case[1], natural_explanation="")


def naturalized_english(case=CORRECTION_CASES[0]):
    return Naturalized(CORRECTION, "en", correction_result(case))


def naturalized_romanian(case=TRANSLATION_CASES[0]):
    return Naturalized(TRANSLATION, "ro", translation_result(case))
