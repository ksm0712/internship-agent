"""Lead ranking: scores drafting candidates by fit, learned from the user's
own send/skip/remove decisions instead of drafting in arbitrary order.

Cold-starts with a hand-weighted heuristic over the same features when there
isn't enough history to train on yet; switches to a logistic regression once
there is. Trains fresh on every call rather than persisting a model — the
training set is one user's own history (tens to low hundreds of examples at
most), so retraining is cheap and there's no stale-model state to manage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sklearn.exceptions import NotFittedError
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics.pairwise import cosine_similarity

MIN_TRAINING_EXAMPLES = 8
MIN_EXAMPLES_PER_CLASS = 3

FEATURE_ORDER = ["text_similarity", "contact_quality", "confidence"]


def _resume_role_similarity(resume_text: str, role_text: str) -> float:
    if not resume_text.strip() or not role_text.strip():
        return 0.0
    vectorizer = TfidfVectorizer(stop_words="english")
    try:
        matrix = vectorizer.fit_transform([resume_text, role_text])
    except ValueError:
        # Both documents were entirely stopwords/empty after vectorization.
        return 0.0
    return float(cosine_similarity(matrix[0], matrix[1])[0, 0])


def _contact_quality(contact_source: str | None) -> float:
    if contact_source == "hunter.io":
        return 1.0
    if contact_source == "guessed generic careers address":
        return 0.3
    return 0.0


def extract_features(resume_text: str, candidate: dict[str, Any]) -> dict[str, float]:
    role_text = " ".join(str(candidate.get(k, "")) for k in ("role", "description"))
    return {
        "text_similarity": _resume_role_similarity(resume_text, role_text),
        "contact_quality": _contact_quality(candidate.get("contact_source")),
        "confidence": float(candidate.get("confidence") or 0.5),
    }


def _vectorize(features: dict[str, float]) -> list[float]:
    return [features[name] for name in FEATURE_ORDER]


@dataclass
class RankResult:
    score: float
    used_model: bool


class LeadRanker:
    def __init__(self, training_examples: list[dict[str, Any]], resume_text: str) -> None:
        self._resume_text = resume_text
        self._model = self._train(training_examples, resume_text)

    @staticmethod
    def _train(examples: list[dict[str, Any]], resume_text: str) -> LogisticRegression | None:
        if len(examples) < MIN_TRAINING_EXAMPLES:
            return None
        labels = [ex["label"] for ex in examples]
        if labels.count(1) < MIN_EXAMPLES_PER_CLASS or labels.count(0) < MIN_EXAMPLES_PER_CLASS:
            return None
        features = [_vectorize(extract_features(resume_text, ex)) for ex in examples]
        model = LogisticRegression(max_iter=1000)
        try:
            model.fit(features, labels)
        except ValueError:
            return None
        return model

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def score(self, candidate: dict[str, Any]) -> RankResult:
        features = extract_features(self._resume_text, candidate)
        if self._model is not None:
            try:
                proba = self._model.predict_proba([_vectorize(features)])[0][1]
                return RankResult(score=float(proba), used_model=True)
            except NotFittedError:
                pass
        heuristic = (
            0.55 * features["text_similarity"]
            + 0.30 * features["contact_quality"]
            + 0.15 * features["confidence"]
        )
        return RankResult(score=heuristic, used_model=False)

    def rank(self, candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], RankResult]]:
        scored = [(c, self.score(c)) for c in candidates]
        scored.sort(key=lambda pair: pair[1].score, reverse=True)
        return scored
