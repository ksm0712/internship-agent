"""Command-line entry point: `python -m internship_agent <command>`.

No concept of multiple users, so drafts/history are scoped under one fixed
key (`CLI_USER_EMAIL`) in the same database the web app uses.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from internship_agent.clients.gmail_client import gmail_service, send_message, setup_gmail
from internship_agent.config import load_config
from internship_agent.db import Database
from internship_agent.paths import (
    DB_PATH,
    DEFAULT_CREDENTIALS_FILE,
    DEFAULT_TOKEN_FILE,
    ensure_dirs,
)
from internship_agent.pipeline.contacts import find_contacts
from internship_agent.pipeline.drafting import draft_emails
from internship_agent.pipeline.search import search_internships
from internship_agent.repository import Repository

CLI_USER_EMAIL = "cli-local"


def prompt_for_resume(path: Path | None) -> Path:
    if path:
        return path.expanduser().resolve()
    value = input("Resume path (.pdf, .txt, or .md): ").strip()
    if not value:
        raise RuntimeError("No resume path provided.")
    return Path(value).expanduser().resolve()


def review_and_send(repo: Repository, credentials_file: Path, token_file: Path) -> None:
    drafts = repo.list_drafts(CLI_USER_EMAIL)
    pending = [d for d in drafts if d["status"] != "sent"]
    if not pending:
        print("No pending drafts.")
        return

    service = None
    for idx, draft in enumerate(pending, start=1):
        print("\n" + "=" * 72)
        print(f"Draft {idx}/{len(pending)}: {draft.get('company')} - {draft.get('role')}")
        print(f"To: {draft.get('to')}")
        print(f"Subject: {draft.get('subject')}")
        if draft.get("resume_path"):
            print(f"Attachment: {draft.get('resume_path')}")
        print("-" * 72)
        print(draft.get("body", ""))
        print("-" * 72)
        answer = input("Send this email? [y]es / [n]o skip / [q]uit: ").strip().lower()
        if answer == "q":
            break
        if answer != "y":
            repo.update_draft_status(CLI_USER_EMAIL, draft["id"], "skipped")
            continue
        if not draft.get("to") or "@" not in draft["to"]:
            print("No valid recipient email; skipping.")
            repo.update_draft_status(CLI_USER_EMAIL, draft["id"], "missing_email")
            continue
        if service is None:
            service = gmail_service(credentials_file, token_file)
        sent = send_message(service, draft["to"], draft["subject"], draft["body"], draft.get("resume_path"))
        repo.update_draft_status(CLI_USER_EMAIL, draft["id"], "sent", gmail_message_id=sent.get("id"))
        print(f"Sent. Gmail message id: {sent.get('id')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find Singapore AI/tech internships and draft approved emails."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="Find current internship opportunities.")
    search_p.add_argument("--limit", type=int, default=25)
    search_p.add_argument("--locations", default="", help="Comma-separated locations, e.g. 'Singapore,Remote'")
    search_p.add_argument("--roles", default="", help="Comma-separated role keywords, e.g. 'machine learning,backend'")

    contacts_p = sub.add_parser("contacts", help="Find recipient emails for saved opportunities.")
    contacts_p.add_argument("--limit", type=int, default=100)

    drafts_p = sub.add_parser("draft", help="Draft personalized emails from resume and contacts.")
    drafts_p.add_argument("--resume", type=Path)
    drafts_p.add_argument("--limit", type=int, default=25)

    sub.add_parser("send", help="Review each draft and send only after approval.")

    setup_p = sub.add_parser("setup-gmail", help="Save the local Gmail OAuth credentials file.")
    setup_p.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS_FILE)

    run_p = sub.add_parser("run", help="Run search, contact lookup, draft, then approval-to-send.")
    run_p.add_argument("--resume", type=Path)
    run_p.add_argument("--limit", type=int, default=15)
    run_p.add_argument("--locations", default="")
    run_p.add_argument("--roles", default="")

    sub.add_parser("stats", help="Print aggregate pipeline metrics.")

    args = parser.parse_args()
    ensure_dirs()
    db = Database(DB_PATH)
    repo = Repository(db)

    try:
        if args.command == "search":
            config = load_config()
            locations = [loc.strip() for loc in args.locations.split(",") if loc.strip()]
            roles = [role.strip() for role in args.roles.split(",") if role.strip()]
            found = search_internships(args.limit, config, repo, locations=locations, roles=roles)
            print(f"Found {len(found)} new opportunities ({repo.count_opportunities()} total saved).")
        elif args.command == "contacts":
            config = load_config()
            opportunities = repo.list_opportunities(args.limit)
            contacts = find_contacts(opportunities, config, repo)
            print(f"Resolved {len(contacts)} contacts.")
        elif args.command == "draft":
            config = load_config()
            resume = prompt_for_resume(args.resume)
            contacts = repo.list_contacts()
            drafts = draft_emails(resume, contacts, args.limit, config, repo, CLI_USER_EMAIL)
            print(f"Drafted {len(drafts)} emails.")
        elif args.command == "send":
            review_and_send(repo, DEFAULT_CREDENTIALS_FILE, DEFAULT_TOKEN_FILE)
        elif args.command == "setup-gmail":
            setup_gmail(args.credentials)
        elif args.command == "run":
            config = load_config()
            resume = prompt_for_resume(args.resume)
            locations = [loc.strip() for loc in args.locations.split(",") if loc.strip()]
            roles = [role.strip() for role in args.roles.split(",") if role.strip()]
            search_internships(args.limit, config, repo, locations=locations, roles=roles)
            contacts = find_contacts(repo.list_opportunities(), config, repo)
            draft_emails(resume, contacts, args.limit, config, repo, CLI_USER_EMAIL)
            review_and_send(repo, DEFAULT_CREDENTIALS_FILE, DEFAULT_TOKEN_FILE)
        elif args.command == "stats":
            from internship_agent.metrics import aggregate_stats

            stats = aggregate_stats(db)
            print(f"Total runs: {stats['total_runs']}")
            for stage, values in stats["stages"].items():
                print(f"\n[{stage}]")
                for key, value in values.items():
                    print(f"  {key}: {value}")
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
