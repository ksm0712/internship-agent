"""Stage 3: draft a personalized outreach email per contact.

Drafting is parallelized across a small thread pool since each Gemini call is
independent per contact. Each draft is written to the database as soon as
it's produced (rather than batched at the end), matching the original agent's
incremental-save behavior so a failure partway through a batch doesn't lose
the drafts that already succeeded.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from internship_agent.clients.gemini_client import GeminiClient
from internship_agent.config import Config
from internship_agent.metrics import StageTimer, new_run_id
from internship_agent.repository import Repository
from internship_agent.resume import fallback_draft, read_resume
from internship_agent.text_utils import company_key

DRAFTING_MAX_WORKERS = 3

_DRAFT_PROMPT = """
Draft a concise, warm cold email for this internship application.

Rules:
- Write in first person as the candidate.
- Keep it under 170 words.
- Make it specific to the company and role.
- Use only resume facts provided below; do not invent experience.
- Ask whether they would consider the candidate for the role or a similar internship.
- Include a short subject line.
- No markdown.

Return ONLY valid JSON:
{{
  "subject": "...",
  "body": "Hi ...\\n\\n...\\n\\nBest,\\n<Candidate name if inferable, else Your Name>"
}}

Resume:
{resume}

Opportunity/contact:
{contact_json}
"""


def _draft_one(
    gemini: GeminiClient, contact: dict[str, Any], resume_text: str, resume_file: Path
) -> tuple[dict[str, str], bool]:
    """Returns (draft {subject, body}, used_fallback)."""
    prompt = _DRAFT_PROMPT.format(
        resume=resume_text[:7000], contact_json=json.dumps(contact, ensure_ascii=False)
    )
    try:
        draft = gemini.generate_json(prompt)
        if "subject" not in draft or "body" not in draft:
            raise ValueError("Gemini draft missing subject/body")
        return draft, False
    except Exception:
        return fallback_draft(contact, resume_text, resume_file), True


def draft_emails(
    resume_file: Path,
    contacts: list[dict[str, Any]],
    limit: int,
    config: Config,
    repo: Repository,
    user_email: str,
    *,
    run_id: str | None = None,
    max_workers: int = DRAFTING_MAX_WORKERS,
) -> list[dict[str, Any]]:
    run_id = run_id or new_run_id()
    gemini = GeminiClient(config.gemini_api_key)
    resume_text = read_resume(resume_file)

    already_drafted = repo.existing_draft_company_keys(user_email)
    candidates = [
        contact
        for contact in contacts[:limit]
        if company_key(contact.get("company")) not in already_drafted
    ]

    with StageTimer(repo.db, run_id=run_id, stage="draft", user_email=user_email) as timer:
        timer.items_in = len(candidates)
        created: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_draft_one, gemini, contact, resume_text, resume_file): contact
                for contact in candidates
            }
            for future in as_completed(futures):
                contact = futures[future]
                try:
                    draft_text, used_fallback = future.result()
                    timer.api_calls += 1
                    if used_fallback:
                        timer.record_error()
                    draft = {
                        "status": "pending_approval",
                        "to": contact.get("email", ""),
                        "recipient_name": contact.get("contact_name") or "Hiring Team",
                        "company": contact.get("company"),
                        "role": contact.get("role"),
                        "source_url": contact.get("source_url"),
                        "contact_source": contact.get("contact_source", ""),
                        "resume_path": str(resume_file),
                        "subject": draft_text["subject"],
                        "body": draft_text["body"],
                    }
                    draft_id = repo.add_draft(user_email, draft)
                    created.append({**draft, "id": draft_id})
                except Exception:
                    timer.record_error()
                    continue

        timer.items_out = len(created)

    return created
