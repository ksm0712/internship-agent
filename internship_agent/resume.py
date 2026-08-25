"""Resume parsing and the offline fallback draft used when Gemini is unavailable."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def read_resume(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Resume not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install pypdf to read PDF resumes: pip install pypdf") from exc
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
        raise RuntimeError("Could not extract text from the PDF resume.")
    raise RuntimeError("Use a .txt, .md, or text-extractable .pdf resume.")


def candidate_name_from_resume(resume_text: str, resume_file: Path | None = None) -> str:
    if resume_file:
        stem = resume_file.stem.replace("_", " ")
        stem = re.sub(r"\bresume\b", "", stem, flags=re.IGNORECASE).strip()
        if stem:
            return " ".join(part.capitalize() for part in stem.split())
    for line in resume_text.splitlines():
        cleaned = line.strip()
        blocked = {"education", "professional experience", "skills, languages & interests"}
        if cleaned.lower() in blocked:
            continue
        if cleaned and len(cleaned.split()) <= 5 and "@" not in cleaned:
            return cleaned.title()
    return "Candidate"


def resume_highlights(resume_text: str, max_items: int = 3) -> list[str]:
    terms = [
        "python",
        "machine learning",
        "artificial intelligence",
        "ai",
        "data",
        "software",
        "react",
        "typescript",
        "javascript",
        "sql",
        "cloud",
        "llm",
        "rag",
        "computer vision",
        "nlp",
    ]
    lowered = resume_text.lower()
    found = []
    for term in terms:
        if term in lowered:
            found.append(term.upper() if term in {"ai", "sql", "llm", "rag", "nlp"} else term)
    return found[:max_items] or ["software engineering", "AI", "data-driven problem solving"]


def fallback_draft(contact: dict[str, Any], resume_text: str, resume_file: Path) -> dict[str, str]:
    """Locally-templated draft used when Gemini is unavailable or over quota.

    Keeps the approval queue usable during an outage instead of blocking the
    whole batch on one flaky LLM call.
    """
    name = candidate_name_from_resume(resume_text, resume_file)
    highlights = ", ".join(resume_highlights(resume_text))
    company = contact.get("company", "your team")
    role = contact.get("role", "internship")
    recipient = contact.get("contact_name") or "Hiring Team"
    greeting = f"Hi {recipient.split()[0]}," if recipient != "Hiring Team" else "Hi Hiring Team,"
    subject = f"Interest in {role} at {company}"
    body = (
        f"{greeting}\n\n"
        f"I hope you are doing well. I came across the {role} opportunity at {company} "
        f"and wanted to reach out because the work sounds closely aligned with my interests "
        f"in {highlights}.\n\n"
        f"I am applying for internships in Singapore and would be excited to "
        f"contribute to {company}'s engineering and AI/data work. My resume is attached, "
        f"and I would be grateful if you would consider me for this role or a similar "
        f"internship opening on your team.\n\n"
        f"Best,\n{name}"
    )
    return {"subject": subject, "body": body}
