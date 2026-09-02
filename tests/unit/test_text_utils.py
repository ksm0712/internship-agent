from __future__ import annotations

import pytest

from internship_agent.text_utils import (
    bare_domain,
    clean_json,
    company_key,
    company_similarity,
    find_fuzzy_duplicate,
    is_relevant_role,
    normalized_url,
    parse_llm_json,
    valid_email,
)


class TestNormalizedUrl:
    def test_lowercases_and_strips_trailing_slash(self):
        assert normalized_url("HTTPS://Example.com/Jobs/") == "https://example.com/jobs"

    def test_none_for_missing_netloc(self):
        assert normalized_url("not-a-url") is None

    def test_none_for_none_input(self):
        assert normalized_url(None) is None


class TestBareDomain:
    def test_extracts_domain_from_full_url(self):
        assert bare_domain("https://www.acme.com/careers") == "acme.com"

    def test_accepts_bare_domain_input(self):
        assert bare_domain("acme.com/jobs") == "acme.com"

    def test_rejects_linkedin(self):
        assert bare_domain("https://www.linkedin.com/company/acme") is None

    def test_rejects_hosts_without_a_dot(self):
        assert bare_domain("localhost") is None

    def test_none_for_none_input(self):
        assert bare_domain(None) is None


class TestCompanyKey:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("Acme Inc.", "acme inc"),
            ("Acme, Inc.", "ACME INC"),
            ("  Acme   Corp  ", "acme corp"),
        ],
    )
    def test_strips_punctuation_and_case(self, a, b):
        assert company_key(a) == company_key(b)

    def test_empty_for_none(self):
        assert company_key(None) == ""


class TestValidEmail:
    @pytest.mark.parametrize("value", ["a@b.com", "first.last@sub.example.co"])
    def test_valid(self, value):
        assert valid_email(value) is True

    @pytest.mark.parametrize("value", [None, "", "not-an-email", "a@b", "@b.com"])
    def test_invalid(self, value):
        assert valid_email(value) is False


class TestCleanJson:
    def test_strips_markdown_fence_with_json_label(self):
        raw = '```json\n{"a": 1}\n```'
        assert clean_json(raw) == '{"a": 1}'

    def test_strips_bare_fence(self):
        raw = '```\n{"a": 1}\n```'
        assert clean_json(raw) == '{"a": 1}'

    def test_passthrough_for_plain_json(self):
        assert clean_json('{"a": 1}') == '{"a": 1}'

    def test_parse_llm_json_roundtrip(self):
        assert parse_llm_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]


class TestIsRelevantRole:
    def _item(self, **overrides):
        base = {
            "company": "Acme",
            "role": "AI Engineering Intern",
            "description": "Work on ML pipelines.",
            "location": "Singapore",
            "source_url": "https://acme.com/careers",
            "evidence": "AI intern role",
        }
        base.update(overrides)
        return base

    def test_rejects_non_internship(self):
        item = self._item(role="Software Engineer", evidence="Full-time engineering role.")
        assert is_relevant_role(item) is False

    def test_rejects_generic_role_titles(self):
        assert is_relevant_role(self._item(role="Careers")) is False

    # ---- no locations/roles given: falls back to "any location" + DEFAULT_ROLE_TERMS ----

    def test_no_filters_accepts_any_tech_internship_anywhere(self):
        item = self._item(location="Memphis, Tennessee")
        assert is_relevant_role(item) is True

    def test_no_filters_rejects_role_without_any_default_tech_term(self):
        item = self._item(
            role="Marketing Intern",
            description="Help with marketing campaigns.",
            evidence="Marketing internship, no tech focus.",
        )
        assert is_relevant_role(item) is False

    # ---- explicit location filter ----

    def test_location_filter_accepts_matching_location(self):
        item = self._item(location="Berlin, Germany")
        assert is_relevant_role(item, locations=["Berlin"]) is True

    def test_location_filter_rejects_non_matching_location(self):
        item = self._item(location="Memphis, Tennessee")
        assert is_relevant_role(item, locations=["Singapore", "Berlin"]) is False

    def test_location_filter_accepts_remote_regardless_of_selection(self):
        item = self._item(location="Remote")
        assert is_relevant_role(item, locations=["Singapore"]) is True

    def test_location_filter_accepts_location_mentioned_in_description(self):
        item = self._item(location="Germany", description="Based out of our Berlin office.")
        assert is_relevant_role(item, locations=["Berlin"]) is True

    # ---- explicit role filter ----

    def test_role_filter_accepts_matching_keyword(self):
        item = self._item(role="Backend Intern", description="Work on backend services.", evidence="backend intern")
        assert is_relevant_role(item, roles=["backend"]) is True

    def test_role_filter_rejects_non_matching_keyword(self):
        item = self._item(
            role="Marketing Intern", description="Support campaigns.", evidence="marketing internship"
        )
        assert is_relevant_role(item, roles=["backend", "machine learning"]) is False

    def test_combined_location_and_role_filters(self):
        item = self._item(
            location="Singapore",
            role="Machine Learning Intern",
            description="Work on ML systems.",
            evidence="machine learning internship",
        )
        assert is_relevant_role(item, locations=["Singapore"], roles=["machine learning"]) is True
        assert is_relevant_role(item, locations=["Berlin"], roles=["machine learning"]) is False
        assert is_relevant_role(item, locations=["Singapore"], roles=["backend"]) is False


class TestFuzzyDedup:
    def test_similarity_is_one_for_identical_keys(self):
        assert company_similarity("Acme Inc.", "ACME, Inc") == 1.0

    def test_similarity_is_zero_for_empty_input(self):
        assert company_similarity("", "Acme") == 0.0

    def test_finds_near_duplicate_company(self):
        known = ["Acme Corp", "Globex", "Initech"]
        assert find_fuzzy_duplicate("Acme Corp.", known) == "Acme Corp"

    def test_does_not_flag_distinct_companies(self):
        known = ["Acme Corp", "Globex", "Initech"]
        assert find_fuzzy_duplicate("Umbrella Corp", known) is None

    def test_respects_custom_threshold(self):
        known = ["Acme"]
        # "Acme Robotics" is not a near-duplicate of "Acme" at a strict threshold.
        assert find_fuzzy_duplicate("Acme Robotics", known, threshold=0.95) is None
