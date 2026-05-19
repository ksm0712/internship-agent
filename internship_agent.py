import argparse
import base64
import json
import os
import re
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

warnings.simplefilter("ignore", FutureWarning)

import google.generativeai as genai
import requests
from dotenv import load_dotenv
from tavily import TavilyClient

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
DEFAULT_SEARCH_FILE = DATA_DIR / "internships.json"
DEFAULT_CONTACTS_FILE = DATA_DIR / "contacts.json"
DEFAULT_DRAFTS_FILE = OUT_DIR / "email_drafts.json"
DEFAULT_TOKEN_FILE = ROOT / "token.json"
DEFAULT_CREDENTIALS_FILE = ROOT / "credentials.json"
MODEL_NAME = "gemini-2.5-flash-lite"


AI_TECH_TERMS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "data science",
    "software",
    "engineering",
    "computer science",
    "robotics",
    "quant",
    "developer",
]


@dataclass
class Config:
    tavily_api_key: str
    gemini_api_key: str
    hunter_api_key: str | None


def load_config() -> Config:
    load_dotenv()
    tavily_key = os.getenv("TAVILY_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not tavily_key:
        raise RuntimeError("Missing TAVILY_API_KEY in .env")
    if not gemini_key:
        raise RuntimeError("Missing GEMINI_API_KEY in .env")
    return Config(
        tavily_api_key=tavily_key,
        gemini_api_key=gemini_key,
        hunter_api_key=os.getenv("HUNTER_API_KEY"),
    )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)


def json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def json_save(path: Path, data: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def clean_json(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    return raw.strip()


def llm_json(model: genai.GenerativeModel, prompt: str) -> Any:
    response = model.generate_content(prompt)
    return json.loads(clean_json(response.text))


def normalized_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()


def bare_domain(url_or_domain: str | None) -> str | None:
    if not url_or_domain:
        return None
    value = url_or_domain.strip()
    if value.lower().startswith("http"):
        host = urlparse(value).netloc
    else:
        host = value.split("/")[0]
    host = host.lower().replace("www.", "")
    if "." not in host or "linkedin.com" in host:
        return None
    return host


def is_relevant_role(item: dict[str, Any]) -> bool:
    role_text = " ".join(
        str(item.get(k, "")).lower() for k in ("role", "description", "evidence")
    )
    full_text = " ".join(
        str(item.get(k, "")).lower()
        for k in ("company", "role", "description", "location", "source_url")
    )
    location = str(item.get("location", "")).lower()
    source_url = str(item.get("source_url", "")).lower()
    singaporeish = (
        "singapore" in location
        or ".sg" in source_url
        or "mycareersfuture.gov.sg" in source_url
        or ("remote" in location and "singapore" in full_text)
    )
    blocked_locations = ["memphis", "tennessee", "usa", "united states"]
    if any(place in location for place in blocked_locations) and "singapore" not in location:
        singaporeish = False
    return (
        re.search(r"\bintern(ship)?\b", role_text) is not None
        and singaporeish
        and any(term in role_text for term in AI_TECH_TERMS)
    )


def search_internships(limit: int) -> list[dict[str, Any]]:
    config = load_config()
    ensure_dirs()
    tavily = TavilyClient(api_key=config.tavily_api_key)
    genai.configure(api_key=config.gemini_api_key)
    model = genai.GenerativeModel(MODEL_NAME)

    current_year = date.today().year
    queries = [
        f"Singapore AI internship summer {current_year}",
        f"Singapore machine learning intern summer {current_year}",
        f"Singapore software engineering intern summer {current_year} startup",
        f"Singapore computer science internship {current_year} AI tech",
        f"site:jobs.lever.co Singapore AI intern {current_year}",
        f"site:greenhouse.io Singapore machine learning intern {current_year}",
        f"site:mycareersfuture.gov.sg AI intern Singapore {current_year}",
        f"site:linkedin.com/jobs Singapore AI intern {current_year}",
    ]

    print("Searching for recent Singapore AI/tech/CS internships...")
    seen_urls: set[str] = set()
    results: list[dict[str, Any]] = []
    for query in queries:
        print(f"  - {query}")
        response = tavily.search(
            query=query,
            max_results=6,
            search_depth="advanced",
            include_answer=False,
        )
        for result in response.get("results", []):
            url = normalized_url(result.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                {
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "content": result.get("content", ""),
                    "score": result.get("score"),
                }
            )

    urls = [r["url"] for r in results[: max(8, min(len(results), 40))]]
    extracted_pages: list[dict[str, str]] = []
    if urls:
        print(f"Extracting page text from {len(urls)} candidate URLs...")
        for start in range(0, len(urls), 20):
            batch = urls[start : start + 20]
            extracted = tavily.extract(urls=batch)
            extracted_pages.extend(extracted.get("results", []))

    snippets = []
    raw_by_url = {page.get("url"): page.get("raw_content", "") for page in extracted_pages}
    for result in results:
        raw = raw_by_url.get(result["url"], "")
        snippets.append(
            {
                "title": result["title"],
                "url": result["url"],
                "search_snippet": result["content"][:900],
                "page_text": raw[:5000],
            }
        )

    prompt = f"""
You are extracting real internship opportunities for a student applying for this summer in Singapore.

Today is {date.today().isoformat()}. Extract ONLY current or plausibly current AI/tech/computer-science-related internships in Singapore, Singapore-hybrid, or remote roles open to Singapore-based applicants.

Reject job boards as companies. Keep the source URL as the page where the role was found.

Return ONLY valid JSON:
[
  {{
    "company": "Company name",
    "role": "Exact internship role title",
    "description": "One sentence about the role and company",
    "location": "Singapore / Remote / Hybrid, as stated",
    "official_url": "Company website if visible, else null",
    "source_url": "URL for the job/source",
    "evidence": "Short evidence phrase from the source",
    "confidence": 0.0
  }}
]

Search and extracted content:
{json.dumps(snippets, ensure_ascii=False)}
"""
    items = llm_json(model, prompt)
    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_generic_companies: set[str] = set()
    for item in items:
        if not is_relevant_role(item):
            continue
        company_key = re.sub(r"\W+", "", item.get("company", "").lower())
        if company_key == "aiap":
            company_key = "aisingapore"
            item["company"] = "AI Singapore"
        role_key = re.sub(r"\W+", "", item.get("role", "").lower())
        generic_role = role_key in {"internship", "intern", "internshipprogramme"}
        if generic_role and company_key in seen_generic_companies:
            continue
        key = (
            company_key,
            role_key,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if generic_role or "internshipprogramme" in role_key:
            seen_generic_companies.add(company_key)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    json_save(DEFAULT_SEARCH_FILE, deduped)
    print(f"Saved {len(deduped)} opportunities to {DEFAULT_SEARCH_FILE}")
    return deduped


def choose_domain(
    tavily: TavilyClient, model: genai.GenerativeModel, company: str, official_url: str | None
) -> str | None:
    direct = bare_domain(official_url)
    if direct:
        return direct

    response = tavily.search(query=f"{company} official website", max_results=5)
    urls = [r.get("url") for r in response.get("results", [])]
    prompt = f"""
Pick the official company website domain for "{company}" from this list.
Reject LinkedIn, job boards, Crunchbase, social media, and news sites.

URLs: {urls}

Return ONLY JSON:
{{"domain": "example.com or null"}}
"""
    try:
        chosen = llm_json(model, prompt)
    except Exception:
        return None
    return bare_domain(chosen.get("domain"))


def hunter_contacts(domain: str, hunter_api_key: str | None) -> list[dict[str, Any]]:
    if not hunter_api_key:
        return []
    url = "https://api.hunter.io/v2/domain-search"
    response = requests.get(
        url,
        params={"domain": domain, "api_key": hunter_api_key},
        timeout=30,
    )
    if response.status_code != 200:
        return []
    return response.json().get("data", {}).get("emails", [])


def score_contact(contact: dict[str, Any]) -> int:
    position = str(contact.get("position", "")).lower()
    email = str(contact.get("value", "")).lower()
    score = int(contact.get("confidence") or 0)
    for keyword, boost in [
        ("talent", 35),
        ("recruit", 35),
        ("people", 25),
        ("founder", 25),
        ("cto", 25),
        ("engineering", 20),
        ("engineer", 15),
        ("hr", 15),
        ("career", 15),
    ]:
        if keyword in position or keyword in email:
            score += boost
    return score


def find_contacts(input_file: Path) -> list[dict[str, Any]]:
    config = load_config()
    ensure_dirs()
    tavily = TavilyClient(api_key=config.tavily_api_key)
    genai.configure(api_key=config.gemini_api_key)
    model = genai.GenerativeModel(MODEL_NAME)

    opportunities = json_load(input_file, [])
    existing = json_load(DEFAULT_CONTACTS_FILE, [])
    existing_by_key = {(c["company"], c["role"]): c for c in existing}
    output = list(existing)

    for opp in opportunities:
        key = (opp["company"], opp["role"])
        if key in existing_by_key:
            continue
        print(f"Finding contact for {opp['company']} - {opp['role']}")
        domain = choose_domain(tavily, model, opp["company"], opp.get("official_url"))
        contact: dict[str, Any] = {
            **opp,
            "domain": domain,
            "contact_name": "",
            "contact_position": "",
            "email": "",
            "contact_source": "",
        }
        if domain:
            emails = hunter_contacts(domain, config.hunter_api_key)
            if emails:
                chosen = sorted(emails, key=score_contact, reverse=True)[0]
                contact.update(
                    {
                        "contact_name": " ".join(
                            str(chosen.get(part, "")).strip()
                            for part in ("first_name", "last_name")
                        ).strip(),
                        "contact_position": chosen.get("position") or "",
                        "email": chosen.get("value") or "",
                        "contact_source": "hunter.io",
                    }
                )
            else:
                contact["email"] = f"careers@{domain}"
                contact["contact_source"] = "guessed generic careers address"
        output.append(contact)
        json_save(DEFAULT_CONTACTS_FILE, output)
        time.sleep(2)

    print(f"Saved {len(output)} contact records to {DEFAULT_CONTACTS_FILE}")
    return output


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


def draft_emails(resume_file: Path, input_file: Path, limit: int) -> list[dict[str, Any]]:
    config = load_config()
    ensure_dirs()
    genai.configure(api_key=config.gemini_api_key)
    model = genai.GenerativeModel(MODEL_NAME)
    resume_text = read_resume(resume_file)
    contacts = json_load(input_file, [])[:limit]
    drafts: list[dict[str, Any]] = []

    for contact in contacts:
        recipient_name = contact.get("contact_name") or "Hiring Team"
        prompt = f"""
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
{resume_text[:7000]}

Opportunity/contact:
{json.dumps(contact, ensure_ascii=False)}
"""
        draft = llm_json(model, prompt)
        drafts.append(
            {
                "status": "pending_approval",
                "to": contact.get("email", ""),
                "recipient_name": recipient_name,
                "company": contact.get("company"),
                "role": contact.get("role"),
                "source_url": contact.get("source_url"),
                "contact_source": contact.get("contact_source", ""),
                "subject": draft["subject"],
                "body": draft["body"],
            }
        )

    json_save(DEFAULT_DRAFTS_FILE, drafts)
    print(f"Saved {len(drafts)} drafts to {DEFAULT_DRAFTS_FILE}")
    return drafts


def gmail_service(credentials_file: Path, token_file: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Install Gmail OAuth dependencies: pip install google-auth-oauthlib"
        ) from exc

    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Missing {credentials_file}. Download an OAuth desktop client JSON "
                    "from Google Cloud and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def send_message(service: Any, to_email: str, subject: str, body: str) -> dict[str, Any]:
    message = EmailMessage()
    message.set_content(body)
    message["To"] = to_email
    message["Subject"] = subject
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": encoded}).execute()


def review_and_send(drafts_file: Path, credentials_file: Path, token_file: Path) -> None:
    drafts = json_load(drafts_file, [])
    if not drafts:
        print(f"No drafts found in {drafts_file}")
        return

    service = None
    changed = False
    for idx, draft in enumerate(drafts, start=1):
        if draft.get("status") == "sent":
            continue
        print("\n" + "=" * 72)
        print(f"Draft {idx}/{len(drafts)}: {draft.get('company')} - {draft.get('role')}")
        print(f"To: {draft.get('to')}")
        print(f"Subject: {draft.get('subject')}")
        print("-" * 72)
        print(draft.get("body", ""))
        print("-" * 72)
        answer = input("Send this email? [y]es / [n]o skip / [q]uit: ").strip().lower()
        if answer == "q":
            break
        if answer != "y":
            draft["status"] = "skipped"
            changed = True
            continue
        if not draft.get("to") or "@" not in draft["to"]:
            print("No valid recipient email; skipping.")
            draft["status"] = "missing_email"
            changed = True
            continue
        if service is None:
            service = gmail_service(credentials_file, token_file)
        sent = send_message(service, draft["to"], draft["subject"], draft["body"])
        draft["status"] = "sent"
        draft["gmail_message_id"] = sent.get("id")
        changed = True
        json_save(drafts_file, drafts)
        print(f"Sent. Gmail message id: {sent.get('id')}")

    if changed:
        json_save(drafts_file, drafts)
        print(f"Updated {drafts_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find Singapore AI/tech internships and draft approved emails.")
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="Find current internship opportunities.")
    search_p.add_argument("--limit", type=int, default=25)

    contacts_p = sub.add_parser("contacts", help="Find recipient emails for saved opportunities.")
    contacts_p.add_argument("--input", type=Path, default=DEFAULT_SEARCH_FILE)

    drafts_p = sub.add_parser("draft", help="Draft personalized emails from resume and contacts.")
    drafts_p.add_argument("--resume", type=Path, required=True)
    drafts_p.add_argument("--input", type=Path, default=DEFAULT_CONTACTS_FILE)
    drafts_p.add_argument("--limit", type=int, default=25)

    send_p = sub.add_parser("send", help="Review each draft and send only after approval.")
    send_p.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS_FILE)
    send_p.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS_FILE)
    send_p.add_argument("--token", type=Path, default=DEFAULT_TOKEN_FILE)

    run_p = sub.add_parser("run", help="Run search, contact lookup, draft, then approval-to-send.")
    run_p.add_argument("--resume", type=Path, required=True)
    run_p.add_argument("--limit", type=int, default=15)
    run_p.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS_FILE)
    run_p.add_argument("--token", type=Path, default=DEFAULT_TOKEN_FILE)

    args = parser.parse_args()
    try:
        if args.command == "search":
            search_internships(args.limit)
        elif args.command == "contacts":
            find_contacts(args.input)
        elif args.command == "draft":
            draft_emails(args.resume, args.input, args.limit)
        elif args.command == "send":
            review_and_send(args.drafts, args.credentials, args.token)
        elif args.command == "run":
            search_internships(args.limit)
            find_contacts(DEFAULT_SEARCH_FILE)
            draft_emails(args.resume, DEFAULT_CONTACTS_FILE, args.limit)
            review_and_send(DEFAULT_DRAFTS_FILE, args.credentials, args.token)
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
