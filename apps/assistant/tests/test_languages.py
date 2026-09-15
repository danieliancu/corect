"""The language registry is the single source of supported languages: schema, prompt, messages and stored types."""
import re
from pathlib import Path
from typing import get_args

from django.conf import settings
from django.test import SimpleTestCase

from apps.analytics.models import UsageEvent
from apps.assistant import languages
from apps.assistant.models import AssistantRequest
from apps.assistant.schemas import NaturalizeResult, SourceLanguageCode
from apps.assistant.services.prompts import LANGUAGE_RULES, NATURALIZE_PROMPT


class LanguageRegistryTests(SimpleTestCase):
    def test_supported_sources_target_and_explanation_language(self):
        self.assertEqual(languages.source_codes(), ("en", "ro"))
        self.assertEqual((languages.TARGET.code, languages.EXPLANATION_LANGUAGE), ("en-GB", "ro"))
        self.assertEqual([languages.operation_for(code) for code in ("en", "ro", "other", "ambiguous", "pl")],
                         ["correction", "translation", None, None, None])
        self.assertEqual(languages.translation_codes(), ("ro",))

    def test_schema_prompt_and_messages_come_from_the_registry(self):
        self.assertEqual(get_args(SourceLanguageCode), languages.source_codes() + languages.UNSUPPORTED)
        self.assertNotIn("original_text", NaturalizeResult.model_fields)  # The input is never echoed back.
        for code, language in languages.SOURCE_LANGUAGES.items():
            with self.subTest(code=code):
                self.assertIn(code, LANGUAGE_RULES)
                self.assertEqual(NATURALIZE_PROMPT.count(f"## If source_language is {code} ({language.name})"), 1)
                self.assertIn(language.label_ro, languages.unsupported_message())
                self.assertIn(language.name, languages.transcription_prompt())

    def test_operations_are_valid_stored_request_types(self):
        for operation in (languages.CORRECTION, languages.TRANSLATION, languages.UNCLASSIFIED):
            self.assertIn(operation, AssistantRequest.Kind.values)
            self.assertIn(operation, UsageEvent.Kind.values)

    def test_language_codes_are_not_compared_outside_the_registry(self):
        pattern = re.compile(r"""[!=]=\s*["'](?:en|ro)["']|["'](?:en|ro)["']\s*[!=]=""")
        offenders = []
        for path in (Path(settings.BASE_DIR) / "apps").rglob("*.py"):
            if {"tests", "migrations", "evals"} & set(path.parts) or path.name in ("languages.py", "prompts.py"):
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual(offenders, [])
