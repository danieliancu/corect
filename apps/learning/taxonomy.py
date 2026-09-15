"""Mistake patterns: reusable, conceptual groupings of corrections within a category.

The correction model picks a pattern key from this list in the same structured response (no extra AI call).
Older corrections, or a key that does not match its category, get a deterministic key from `derive_pattern`.
"""
import re
from difflib import SequenceMatcher

# key: (category, Romanian label, short English hint for the prompts)
PATTERNS = {
    "past_simple_vs_present_perfect": ("verb_tense", "Past simple / present perfect", "past simple vs present perfect"),
    "present_perfect_duration": ("verb_tense", "Present perfect cu since / for", "present perfect for duration up to now"),
    "present_simple_vs_continuous": ("verb_tense", "Present simple / continuous", "present simple vs present continuous"),
    "future_forms": ("verb_tense", "Viitorul", "will / going to / present for future"),
    "tense_time_expression_mismatch": ("verb_tense", "Timpul verbului și expresia de timp", "tense contradicts a time word"),
    "verb_tense_other": ("verb_tense", "Timpul verbului", "other tense mistake"),
    "base_form_after_did": ("verb_form", "Forma de bază după did / didn't", "base form after did/didn't/does"),
    "base_form_after_modal": ("verb_form", "Forma de bază după can, will, should", "base form after a modal verb"),
    "past_participle_form": ("verb_form", "Participiul trecut", "past participle after have/has/had or be"),
    "gerund_vs_infinitive": ("verb_form", "-ing sau to + verb", "gerund vs infinitive"),
    "irregular_past_form": ("verb_form", "Verbe neregulate la trecut", "irregular past forms"),
    "verb_form_other": ("verb_form", "Forma verbului", "other verb form mistake"),
    "missing_article": ("article", "Articol lipsă", "missing a/an/the"),
    "unnecessary_article": ("article", "Articol în plus", "article where none is needed"),
    "a_vs_an": ("article", "A / an", "a vs an"),
    "a_vs_the": ("article", "A / the", "a/an vs the"),
    "article_other": ("article", "Articole", "other article mistake"),
    "since_vs_for": ("preposition", "Since / for", "since vs for"),
    "in_on_at_time": ("preposition", "In / on / at pentru timp", "in/on/at with times and dates"),
    "in_on_at_place": ("preposition", "In / on / at pentru loc", "in/on/at with places"),
    "dependent_preposition": ("preposition", "Prepoziția care însoțește un cuvânt", "preposition after a verb/adjective, e.g. interested in"),
    "preposition_other": ("preposition", "Prepoziții", "other preposition mistake"),
    "question_word_order": ("word_order", "Ordinea cuvintelor în întrebări", "word order in questions"),
    "adverb_position": ("word_order", "Locul adverbului", "adverb position"),
    "adjective_order": ("word_order", "Ordinea adjectivelor", "adjective order"),
    "word_order_other": ("word_order", "Ordinea cuvintelor", "other word order mistake"),
    "third_person_s": ("subject_verb_agreement", "-s la persoana a III-a", "he/she/it + verb-s"),
    "there_is_vs_there_are": ("subject_verb_agreement", "There is / there are", "there is vs there are"),
    "subject_verb_agreement_other": ("subject_verb_agreement", "Acord subiect–verb", "other agreement mistake"),
    "second_conditional_form": ("conditional", "Condiționalul imaginar (if + trecut)", "second conditional"),
    "first_conditional_form": ("conditional", "Condiționalul real (if + prezent)", "first conditional"),
    "conditional_other": ("conditional", "Condițional", "other conditional mistake"),
    "make_vs_do": ("collocation", "Make / do", "make vs do"),
    "take_vs_make": ("collocation", "Take / make", "take vs make, e.g. take a photo"),
    "collocation_other": ("collocation", "Colocații", "other collocation"),
    "spelling_other": ("spelling", "Ortografie", "spelling"),
    "punctuation_other": ("punctuation", "Punctuație", "punctuation"),
    "false_friend": ("vocabulary", "Cuvinte care seamănă cu româna", "false friend"),
    "borrow_vs_lend": ("vocabulary", "Borrow / lend", "borrow vs lend"),
    "vocabulary_other": ("vocabulary", "Vocabular", "other word choice"),
    "american_spelling_preference": ("british_english", "Ortografie britanică", "American vs British spelling"),
    "american_vocabulary_preference": ("british_english", "Cuvinte britanice", "American vs British vocabulary"),
    "age_with_be": ("romanian_transfer", "Vârsta cu to be", "I am 30 years old, not I have 30 years"),
    "agree_without_be": ("romanian_transfer", "I agree (fără am)", "I agree, not I'm agree"),
    "literal_translation_ro": ("romanian_transfer", "Traducere cuvânt cu cuvânt", "literal Romanian construction"),
    "romanian_transfer_other": ("romanian_transfer", "Influența limbii române", "other Romanian transfer"),
    "other": ("other", "Altele", "other"),
}
PATTERN_KEYS = tuple(PATTERNS)
MODALS = {"can", "could", "will", "would", "should", "must", "may", "might", "shall"}
TIME_WORDS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "morning", "afternoon",
              "evening", "night", "weekend", "christmas", "january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december", "o'clock", "noon", "midnight", "time"}


def fallback_pattern(category):
    return "other" if category == "other" else f"{category}_other" if f"{category}_other" in PATTERNS else "other"


def pattern_category(key):
    return PATTERNS.get(key, ("other",))[0]


def pattern_label(key):
    return PATTERNS[key][1] if key in PATTERNS else PATTERNS["other"][1]


def pattern_hint(key):
    return PATTERNS[key][2] if key in PATTERNS else PATTERNS["other"][2]


def valid_pattern(category, key):
    """The key when it belongs to the category, otherwise that category's catch-all key."""
    return key if key in PATTERNS and pattern_category(key) == category else fallback_pattern(category)


def _words(text):
    return re.findall(r"[a-z']+|\d+", (text or "").replace("’", "'").lower())


def derive_pattern(category, original, replacement):
    """A deterministic pattern for a correction, from its category and the words that changed."""
    before, after = _words(original), _words(replacement)
    removed, added = set(before) - set(after), set(after) - set(before)
    changed = removed | added
    if category == "preposition":
        if {"since", "for"} & removed and {"since", "for"} & added:
            return "since_vs_for"
        if removed & {"in", "on", "at"} and added & {"in", "on", "at"}:
            return "in_on_at_time" if (set(before) | set(after)) & TIME_WORDS or any(w.isdigit() for w in after) else "in_on_at_place"
        return "preposition_other"
    if category == "article":
        articles = {"a", "an", "the"}
        if added & articles and not removed & articles:
            return "missing_article"
        if removed & articles and not added & articles:
            return "unnecessary_article"
        if {"a", "an"} <= changed:
            return "a_vs_an"
        if changed & {"a", "an"} and "the" in changed:
            return "a_vs_the"
        return "article_other"
    if category == "verb_form":
        if set(before) & {"did", "didn't", "does", "doesn't", "do", "don't"}:
            return "base_form_after_did"
        if set(before) & MODALS:
            return "base_form_after_modal"
        return "verb_form_other"
    if category == "verb_tense":
        if (set(before) | set(after)) & {"since", "for"} and added & {"have", "has", "'ve", "i've", "been", "ve"}:
            return "present_perfect_duration"
        if added & {"have", "has", "i've", "ve", "'ve"} or removed & {"have", "has", "i've", "ve", "'ve"}:
            return "past_simple_vs_present_perfect"
        return "verb_tense_other"
    if category == "word_order":
        return "question_word_order" if "?" in (original or "") + (replacement or "") or (before and before[0] in {
            "where", "what", "when", "why", "who", "how", "which"}) else "word_order_other"
    if category == "subject_verb_agreement":
        if "there" in before and changed & {"is", "are"}:
            return "there_is_vs_there_are"
        if any(a.endswith("s") and a[:-1] in removed or a.endswith("es") and a[:-2] in removed for a in added):
            return "third_person_s"
        return "subject_verb_agreement_other"
    if category == "conditional":
        return "second_conditional_form" if "if" in before and "would" in set(before) | set(after) else "conditional_other"
    if category == "collocation":
        if changed & {"take", "took", "taken"} and changed & {"make", "made"}:
            return "take_vs_make"
        if changed & {"make", "made"} and changed & {"do", "did", "done"}:
            return "make_vs_do"
        return "collocation_other"
    if category == "vocabulary":
        return "borrow_vs_lend" if changed & {"borrow", "lend"} else "vocabulary_other"
    if category == "romanian_transfer":
        if "years" in before and "have" in removed:
            return "age_with_be"
        if "agree" in before and changed & {"am", "i'm", "m"}:
            return "agree_without_be"
        return "romanian_transfer_other"
    if category == "british_english":
        similar = SequenceMatcher(None, (original or "").lower(), (replacement or "").lower()).ratio() >= 0.7
        return "american_spelling_preference" if similar else "american_vocabulary_preference"
    return fallback_pattern(category)


def prompt_pattern_list():
    """Pattern keys grouped by category, for the correction prompt."""
    grouped = {}
    for key, (category, _, _) in PATTERNS.items():
        grouped.setdefault(category, []).append(key)
    return "\n".join(f"{category}: {', '.join(keys)}" for category, keys in grouped.items())
