"""The live language-quality dataset is well formed. This proves nothing about live AI quality; eval_naturalize does."""
from collections import Counter

from django.conf import settings
from django.test import SimpleTestCase

from apps.assistant.evals.dataset import GROUPS, load_cases
from apps.assistant.services.openai_client import INSTRUCTION_PATTERN

MINIMUMS = {"en_romanian_transfer": 50, "en_contexts": 30, "en_tense_time": 8, "en_punctuation_only": 8,
            "en_already_natural": 25, "en_correct_unnatural": 20, "en_american": 10, "en_mixed_ro_words": 6,
            "ro_to_en": 40, "ro_intent_first": 30, "unsupported": 4, "long": 3, "polite": 12}


class EvalDatasetTests(SimpleTestCase):
    def setUp(self):
        self.cases = load_cases()

    def test_the_dataset_is_large_and_covers_every_group(self):
        self.assertGreaterEqual(len(self.cases), 220)
        counts = Counter(case.group for case in self.cases)
        self.assertEqual(set(counts), set(GROUPS))
        for group, minimum in MINIMUMS.items():
            with self.subTest(group=group):
                self.assertGreaterEqual(counts[group], minimum)

    def test_ids_are_unique_and_inputs_fit_the_text_box(self):
        ids = [case.id for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.cases:
            with self.subTest(case=case.id):
                self.assertLessEqual(len(case.input), settings.ASSISTANT_MAX_CHARACTERS)
                self.assertIsNone(INSTRUCTION_PATTERN.search(case.input))
        self.assertGreaterEqual(sum(len(case.input) >= 1500 for case in self.cases), 2)

    def test_expectations_are_consistent_with_each_group(self):
        for case in self.cases:
            expect = case.expect
            with self.subTest(case=case.id):
                if case.group == "unsupported":
                    self.assertEqual((expect.error_code, expect.operation), ("language", None))
                elif expect.operation == "translation":
                    self.assertEqual(expect.source_language, "ro")
                    self.assertIsNone(expect.has_errors)
                    self.assertEqual(expect.corrected_includes_any, [])
                else:
                    self.assertEqual((expect.source_language, expect.operation), ("en", "correction"))
                if expect.natural == "forbidden":
                    self.assertIs(expect.has_errors, False)
                if case.group == "en_already_natural":
                    self.assertEqual((expect.has_errors, expect.natural, expect.max_corrections), (False, "forbidden", 0))
                if case.group == "en_american":
                    self.assertIs(expect.has_errors, False)
                    self.assertTrue(expect.corrected_includes_any)
                if case.group in ("ro_to_en", "ro_intent_first"):
                    self.assertEqual(expect.operation, "translation")
                    self.assertTrue(expect.output_includes_any)
                self.assertEqual(case.polite, case.group == "polite")
                if case.polite and expect.operation == "correction":
                    self.assertEqual(expect.natural, "required")  # Mod Politicos always gives a natural version.

    def test_intent_first_cases_guard_both_literal_translation_and_invented_meaning(self):
        cases = [case for case in self.cases if case.group == "ro_intent_first"]
        self.assertLessEqual(len(cases), 50)
        self.assertGreaterEqual(sum(bool(case.expect.must_not_invent) for case in cases), 5)  # Counterexamples.
        self.assertGreaterEqual(sum(bool(case.expect.output_excludes) for case in cases), 30)  # Literal renderings.
        self.assertGreaterEqual(len({case.context for case in cases}), 8)
        self.assertGreaterEqual(sum(case.input.isascii() for case in cases), 3)  # Typed without diacritics.
        by_input = {case.input: case.expect for case in cases}
        lift = by_input["Vrei să mergi cu mine cu mașina diseară, la 5?"]  # The original literal translation.
        self.assertEqual(lift.output_includes_any[0], ["lift"])
        self.assertIn("in the car", lift.output_excludes)
        self.assertIn("lift", by_input["Vrei să vii cu mine diseară la 5?"].must_not_invent)  # No car, no lift.
        for case in self.cases:
            with self.subTest(case=case.id):
                self.assertFalse(set(case.expect.must_not_invent) & set(case.expect.output_excludes))
