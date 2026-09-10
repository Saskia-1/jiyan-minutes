"""Tests for exception redaction at the public boundary."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.security.redaction import redact_exception


class RedactionTests(unittest.TestCase):
    def test_exception_details_do_not_reach_public_error_or_serialized_data(self) -> None:
        """Returning an upstream exception message could disclose a secret."""
        sentinel = "never-expose-secret-value"

        public = redact_exception(RuntimeError(f"upstream token={sentinel}"))

        self.assertEqual(public.code, "internal_error")
        self.assertNotIn(sentinel, repr(public))
        self.assertNotIn(sentinel, str(public))
        self.assertNotIn(sentinel, json.dumps(public.model_dump(mode="json")))
