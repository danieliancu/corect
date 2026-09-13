"""Contract fixtures, not evidence of live AI language quality."""
from apps.assistant.schemas import CorrectionResult, TranslationResult

CORRECTION_CASES = [
    ("I didn't went to work yesterday.", "I didn't go to work yesterday.", "didn't went", "didn't go", "verb_form", "După did/didn't folosim forma de bază a verbului."),
    ("I'm agree with you.", "I agree with you.", "I'm agree", "I agree", "romanian_transfer", "Agree este deja verb, deci nu folosim am."),
    ("I have 48 years.", "I'm 48 years old.", "I have 48 years", "I'm 48 years old", "romanian_transfer", "În română spunem «am 48 de ani», dar în engleză vârsta se exprimă cu to be."),
    ("She can sings very well.", "She can sing very well.", "can sings", "can sing", "verb_form", "După can folosim forma de bază a verbului."),
    ("I've lived here for five years.", "I've lived here for five years.", "", "", "other", "Textul este deja corect."),
    ("If I had more money, I would buy a new car.", "If I had more money, I would buy a new car.", "", "", "other", "Textul este deja corect."),
    ("I made a photo yesterday.", "I took a photo yesterday.", "made a photo", "took a photo", "collocation", "În engleză spunem take a photo, nu make a photo."),
]
TRANSLATION_CASES = [
    ("Îmi pare rău că n-am putut să ajung mai devreme.", "I'm sorry I couldn't get here earlier.", "ro", "en"),
    ("Dacă aș avea mai mulți bani, aș cumpăra mașina asta.", "If I had more money, I'd buy this car.", "ro", "en"),
    ("I've been living in London for five years.", "Locuiesc în Londra de cinci ani.", "en", "ro"),
]


def correction_result(case=CORRECTION_CASES[0]):
    text, corrected, original, replacement, category, explanation = case
    return CorrectionResult(detected_language="en", original_text=text, corrected_text=corrected,
        has_errors=bool(original), overall_explanation=explanation, native_text="", native_explanation="",
        corrections=[dict(original=original, replacement=replacement, category=category, severity="minor",
                         explanation_ro=explanation, is_british_english_preference=False)] if original else [])


def translation_result(case=TRANSLATION_CASES[0]):
    text, translated, source, target = case
    return TranslationResult(original_text=text, translated_text=translated, source_language=source, target_language=target)
