from __future__ import annotations

from internship_agent.ranking import (
    LeadRanker,
    _contact_quality,
    _resume_role_similarity,
    extract_features,
)

RESUME = (
    "Experienced in Python, machine learning, and backend systems. "
    "Built REST APIs, worked with SQL databases and Docker."
)


def _candidate(**overrides):
    base = {
        "company": "Acme",
        "role": "Backend Engineering Intern",
        "description": "Build backend APIs in Python.",
        "contact_source": "hunter.io",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


class TestResumeRoleSimilarity:
    def test_similar_text_scores_higher_than_dissimilar(self):
        backend_role = "Backend Python engineer building REST APIs and SQL databases"
        marketing_role = "Marketing intern writing social media content and newsletters"
        backend_score = _resume_role_similarity(RESUME, backend_role)
        marketing_score = _resume_role_similarity(RESUME, marketing_role)
        assert backend_score > marketing_score

    def test_empty_inputs_score_zero(self):
        assert _resume_role_similarity("", "some role") == 0.0
        assert _resume_role_similarity(RESUME, "") == 0.0

    def test_identical_text_scores_near_one(self):
        assert _resume_role_similarity(RESUME, RESUME) > 0.99


class TestContactQuality:
    def test_hunter_scores_highest(self):
        assert _contact_quality("hunter.io") == 1.0

    def test_guessed_address_scores_lower(self):
        assert _contact_quality("guessed generic careers address") == 0.3

    def test_unknown_or_missing_scores_zero(self):
        assert _contact_quality(None) == 0.0
        assert _contact_quality("") == 0.0


class TestExtractFeatures:
    def test_returns_all_expected_keys(self):
        features = extract_features(RESUME, _candidate())
        assert set(features.keys()) == {"text_similarity", "contact_quality", "confidence"}

    def test_missing_confidence_defaults_to_midpoint(self):
        features = extract_features(RESUME, _candidate(confidence=None))
        assert features["confidence"] == 0.5


class TestLeadRankerColdStart:
    def test_falls_back_to_heuristic_with_no_history(self):
        ranker = LeadRanker([], RESUME)
        assert ranker.is_trained is False
        result = ranker.score(_candidate())
        assert result.used_model is False
        assert 0.0 <= result.score <= 1.0

    def test_falls_back_with_too_few_examples(self):
        examples = [
            {"role": "Backend Intern", "description": "APIs", "confidence": 0.8, "contact_source": "hunter.io", "label": 1}
            for _ in range(3)
        ]
        ranker = LeadRanker(examples, RESUME)
        assert ranker.is_trained is False

    def test_falls_back_when_only_one_class_present(self):
        examples = [
            {"role": "Backend Intern", "description": "APIs", "confidence": 0.8, "contact_source": "hunter.io", "label": 1}
            for _ in range(10)
        ]
        ranker = LeadRanker(examples, RESUME)
        assert ranker.is_trained is False

    def test_heuristic_prefers_better_text_match_and_contact_quality(self):
        ranker = LeadRanker([], RESUME)
        good = _candidate(role="Backend Python Intern", contact_source="hunter.io")
        bad = _candidate(
            role="Marketing Intern",
            description="Write newsletters and social posts",
            contact_source="guessed generic careers address",
        )
        assert ranker.score(good).score > ranker.score(bad).score


class TestLeadRankerTrained:
    def _training_set(self):
        examples = []
        for _ in range(6):
            examples.append(
                {
                    "role": "Backend Engineering Intern",
                    "description": "Build Python backend services and REST APIs.",
                    "confidence": 0.9,
                    "contact_source": "hunter.io",
                    "label": 1,
                }
            )
        for _ in range(6):
            examples.append(
                {
                    "role": "Marketing Intern",
                    "description": "Write social media content and newsletters.",
                    "confidence": 0.6,
                    "contact_source": "guessed generic careers address",
                    "label": 0,
                }
            )
        return examples

    def test_trains_with_enough_balanced_examples(self):
        ranker = LeadRanker(self._training_set(), RESUME)
        assert ranker.is_trained is True

    def test_trained_model_ranks_matching_role_higher(self):
        ranker = LeadRanker(self._training_set(), RESUME)
        backend_candidate = _candidate(role="Backend Engineering Intern")
        marketing_candidate = _candidate(
            role="Marketing Intern",
            description="Write social media content and newsletters.",
            contact_source="guessed generic careers address",
        )
        backend_result = ranker.score(backend_candidate)
        marketing_result = ranker.score(marketing_candidate)
        assert backend_result.used_model is True
        assert backend_result.score > marketing_result.score

    def test_rank_orders_candidates_by_score_descending(self):
        ranker = LeadRanker(self._training_set(), RESUME)
        backend_candidate = _candidate(role="Backend Engineering Intern")
        marketing_candidate = _candidate(
            role="Marketing Intern",
            description="Write social media content and newsletters.",
            contact_source="guessed generic careers address",
        )
        ranked = ranker.rank([marketing_candidate, backend_candidate])
        assert ranked[0][0]["role"] == "Backend Engineering Intern"
        assert ranked[0][1].score >= ranked[1][1].score
