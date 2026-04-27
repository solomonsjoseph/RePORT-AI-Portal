"""Tests for scripts/utils/log_hygiene.py (Stage 2d)."""

from __future__ import annotations

import logging
import re

import pytest

from scripts.utils import log_hygiene

TEST_KEY = bytes.fromhex("11" * 32)


class TestPHIRedactingFilterGeneric:
    @pytest.fixture()
    def flt(self) -> log_hygiene.PHIRedactingFilter:
        return log_hygiene.PHIRedactingFilter(hmac_key=TEST_KEY)

    def test_aadhaar_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("citizen 1234 5678 9012 enrolled", flt)
        assert "1234 5678 9012" not in out
        assert "<AADHAAR>" in out

    def test_pan_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("PAN: ABCDE1234F applies", flt)
        assert "ABCDE1234F" not in out
        assert "<PAN>" in out

    def test_email_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("contact a.b@example.com today", flt)
        assert "a.b@example.com" not in out
        assert "<EMAIL>" in out

    def test_indian_phone_redacted(
        self, flt: log_hygiene.PHIRedactingFilter
    ) -> None:
        out = log_hygiene._redact("call +91 9876543210 soon", flt)
        assert "9876543210" not in out
        assert "<INDIAN_PHONE>" in out

    def test_date_iso_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("event on 2014-07-15 recorded", flt)
        assert "2014-07-15" not in out
        assert "<DATE_ISO>" in out

    def test_date_mdy_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("on 7/15/2014 patient visited", flt)
        assert "7/15/2014" not in out
        assert "<DATE_MDY>" in out

    def test_pincode_redacted(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        out = log_hygiene._redact("pincode 560001 reached", flt)
        assert "560001" not in out
        assert "<INDIAN_PIN>" in out

    def test_clean_text_passthrough(
        self, flt: log_hygiene.PHIRedactingFilter
    ) -> None:
        msg = "Pipeline step completed successfully"
        assert log_hygiene._redact(msg, flt) == msg


class TestPHIRedactingFilterSubjectIds:
    @pytest.fixture()
    def flt(self) -> log_hygiene.PHIRedactingFilter:
        patterns = [re.compile(r"\bSC\d{4}\b"), re.compile(r"\bSUBJ-\d+\b")]
        return log_hygiene.PHIRedactingFilter(
            hmac_key=TEST_KEY, subject_id_patterns=patterns
        )

    def test_subject_id_replaced_with_hmac_tag(
        self, flt: log_hygiene.PHIRedactingFilter
    ) -> None:
        out = log_hygiene._redact("row for SC1234 processed", flt)
        assert "SC1234" not in out
        assert "<SUBJ_" in out

    def test_same_id_same_tag(self, flt: log_hygiene.PHIRedactingFilter) -> None:
        a = log_hygiene._redact("SC1234", flt)
        b = log_hygiene._redact("SC1234 and SC1234 again", flt)
        # Both redactions use the same HMAC tag.
        tag_a = a.replace("<SUBJ_", "").replace(">", "")
        assert tag_a in b

    def test_different_ids_different_tags(
        self, flt: log_hygiene.PHIRedactingFilter
    ) -> None:
        a = log_hygiene._redact("SC1234", flt)
        b = log_hygiene._redact("SC9999", flt)
        assert a != b


class TestInstallPhiRedactor:
    def test_install_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = logging.getLogger()
        original_filters = list(root.filters)

        try:
            a = log_hygiene.install_phi_redactor(hmac_key=TEST_KEY)
            b = log_hygiene.install_phi_redactor(hmac_key=TEST_KEY)
            assert a is b
            # Root logger has exactly one PHIRedactingFilter attached.
            matching = [
                f for f in root.filters if isinstance(f, log_hygiene.PHIRedactingFilter)
            ]
            assert len(matching) == 1
        finally:
            # Restore original filters.
            root.filters = original_filters

    def test_filter_applies_to_log_output(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        flt = log_hygiene.PHIRedactingFilter(hmac_key=TEST_KEY)
        logger = logging.getLogger("test_log_hygiene.apply")
        logger.addFilter(flt)
        logger.setLevel(logging.INFO)
        with caplog.at_level(logging.INFO, logger=logger.name):
            logger.info("processing row with email patient@example.com now")
        logger.removeFilter(flt)
        assert any("<EMAIL>" in r.getMessage() for r in caplog.records)
        assert not any(
            "patient@example.com" in r.getMessage() for r in caplog.records
        )


class TestBestEffortOnFailure:
    def test_malformed_format_args_do_not_crash(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        flt = log_hygiene.PHIRedactingFilter(hmac_key=TEST_KEY)
        logger = logging.getLogger("test_log_hygiene.errors")
        logger.addFilter(flt)
        logger.setLevel(logging.INFO)
        with caplog.at_level(logging.INFO, logger=logger.name):
            # Intentionally pass wrong number of args — getMessage() will
            # raise internally; filter must fall through without aborting.
            logger.info("hello %s %s", "one")
        logger.removeFilter(flt)
        # A record was still emitted (not dropped).
        assert len(caplog.records) == 1
