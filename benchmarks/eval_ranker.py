"""Evaluation harness for LeadRanker: does it actually separate good-fit
leads from bad ones?

Builds a synthetic labeled dataset (clearly synthetic — this is not a claim
about real production accuracy, since a real user's history doesn't exist
until they've used the app) of backend/ML-leaning postings labeled positive
and sales/marketing-leaning postings labeled negative, against a backend/ML
resume. Trains on a split, evaluates ranking quality on the held-out half
with ROC-AUC: the probability a random positive example is scored higher
than a random negative one (0.5 = coin flip, 1.0 = perfect separation).

Usage:
    python -m benchmarks.eval_ranker
"""
from __future__ import annotations

import random

from sklearn.metrics import roc_auc_score

from internship_agent.ranking import LeadRanker, extract_features

RESUME = """
Jane Doe — B.S. Computer Science

Professional Experience
Built a Python/Flask backend service with a RAG search pipeline over internal
documents. Designed REST APIs, wrote SQL queries against Postgres, used
Docker for local development, and worked with React/TypeScript on the
frontend. Comfortable with distributed systems concepts: caching, retries,
rate limiting, concurrency.
"""

POSITIVE_ROLE_TEMPLATES = [
    ("Backend Engineering Intern", "Build and maintain REST APIs in Python and Flask."),
    ("Machine Learning Intern", "Train and evaluate models, work with data pipelines in Python."),
    ("Platform Engineering Intern", "Improve service reliability: caching, retries, rate limiting."),
    ("Full-Stack Intern", "Work across a React frontend and a Python/Postgres backend."),
    ("Data Engineering Intern", "Build ETL pipelines and SQL data models."),
    ("DevOps Intern", "Manage Docker deployments and CI pipelines for backend services."),
    ("Software Engineering Intern", "Ship features across a Python service and its REST API."),
    ("Infrastructure Intern", "Work on distributed systems: concurrency, queues, and caching layers."),
    ("Search Engineering Intern", "Build retrieval and ranking pipelines over internal documents."),
    ("Site Reliability Intern", "Instrument services and improve rate limiting and retry behavior."),
    ("API Platform Intern", "Design and document REST APIs consumed by internal teams."),
    ("Database Engineering Intern", "Optimize SQL queries and Postgres schema design."),
    ("Frontend Engineering Intern", "Build React/TypeScript interfaces on top of a Flask backend."),
    ("Applied ML Intern", "Prototype data pipelines and model training workflows in Python."),
    ("Cloud Engineering Intern", "Containerize services with Docker and manage deployments."),
]

NEGATIVE_ROLE_TEMPLATES = [
    ("Sales Development Intern", "Cold call prospective customers and qualify leads."),
    ("Marketing Intern", "Write social media content and manage newsletters."),
    ("HR Intern", "Support recruiting coordination and onboarding paperwork."),
    ("Retail Operations Intern", "Assist with in-store inventory and merchandising."),
    ("Finance Intern", "Reconcile invoices and support the accounts payable team."),
    ("Executive Assistant Intern", "Manage calendars and travel for senior leadership."),
    ("Public Relations Intern", "Draft press releases and coordinate media outreach."),
    ("Event Planning Intern", "Coordinate logistics and vendors for corporate events."),
    ("Customer Support Intern", "Answer support tickets and phone calls from customers."),
    ("Legal Intern", "Review contracts and assist paralegal staff with filings."),
    ("Merchandising Intern", "Plan seasonal product assortments for retail stores."),
    ("Talent Acquisition Intern", "Screen resumes and schedule candidate interviews."),
    ("Accounting Intern", "Prepare expense reports and assist with payroll processing."),
    ("Brand Marketing Intern", "Coordinate influencer partnerships and brand campaigns."),
    ("Office Administration Intern", "Manage supplies, mail, and front-desk operations."),
]


def _synthetic_examples(n_per_class: int, seed: int) -> list[dict]:
    # contact_source/confidence are randomized independent of label, not tied
    # to it — otherwise contact_quality alone would trivially predict the
    # label and the AUC below would say nothing about whether the resume/role
    # text-similarity feature (the actually interesting one) carries signal.
    rng = random.Random(seed)
    contact_sources = ["hunter.io", "guessed generic careers address"]
    examples = []
    for _ in range(n_per_class):
        role, description = rng.choice(POSITIVE_ROLE_TEMPLATES)
        examples.append(
            {
                "role": role,
                "description": description,
                "confidence": rng.uniform(0.5, 0.95),
                "contact_source": rng.choice(contact_sources),
                "label": 1,
            }
        )
        role, description = rng.choice(NEGATIVE_ROLE_TEMPLATES)
        examples.append(
            {
                "role": role,
                "description": description,
                "confidence": rng.uniform(0.5, 0.95),
                "contact_source": rng.choice(contact_sources),
                "label": 0,
            }
        )
    return examples


def main() -> None:
    all_examples = _synthetic_examples(n_per_class=60, seed=7)
    random.Random(11).shuffle(all_examples)
    split = len(all_examples) // 2
    train, test = all_examples[:split], all_examples[split:]

    ranker = LeadRanker(train, RESUME)
    print(f"training examples: {len(train)}  |  trained model: {ranker.is_trained}")

    scores = [ranker.score(ex).score for ex in test]
    labels = [ex["label"] for ex in test]
    auc = roc_auc_score(labels, scores)

    print(f"held-out examples: {len(test)}")
    print(f"ROC-AUC (0.5 = random, 1.0 = perfect separation): {auc:.3f}")
    print()
    print("Sample feature vectors:")
    for ex in test[:4]:
        features = extract_features(RESUME, ex)
        print(f"  label={ex['label']}  role={ex['role']!r:35}  features={features}")


if __name__ == "__main__":
    main()
