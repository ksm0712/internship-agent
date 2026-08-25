import os
import re
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from werkzeug.utils import secure_filename

from internship_agent import (
    DB_PATH,
    DEFAULT_CREDENTIALS_FILE,
    UPLOAD_DIR,
    Config,
    ensure_dirs,
)
from internship_agent.clients.gmail_client import send_message
from internship_agent.db import Database
from internship_agent.metrics import aggregate_stats, new_run_id
from internship_agent.pipeline.contacts import find_contacts
from internship_agent.pipeline.drafting import draft_emails
from internship_agent.pipeline.search import search_internships
from internship_agent.repository import Repository
from internship_agent.text_utils import company_key, valid_email

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "local-dev-change-me")
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.send",
]

app = Flask(__name__)
app.secret_key = SECRET_KEY

db = Database(DB_PATH)
repo = Repository(db)


def user_key() -> str:
    email = current_user_email() or "local"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", email).strip("_") or "local"


def current_user_email() -> str | None:
    user = session.get("user") or {}
    return user.get("email")


def resume_display_name(resume_path: str | None) -> str | None:
    if not resume_path:
        return None
    return Path(resume_path).name.replace("_", " ")


def api_key_status() -> dict[str, bool]:
    email = current_user_email()
    if not email:
        return {"gemini": False, "tavily": False, "hunter": False}
    user = repo.get_user(email) or {}
    return {
        "gemini": bool(user.get("gemini_api_key")),
        "tavily": bool(user.get("tavily_api_key")),
        "hunter": bool(user.get("hunter_api_key")),
    }


def user_config(require_search: bool = False, require_draft: bool = False) -> Config:
    email = current_user_email()
    user = (repo.get_user(email) if email else None) or {}
    gemini_key = user.get("gemini_api_key")
    tavily_key = user.get("tavily_api_key")
    if require_draft and not gemini_key:
        raise RuntimeError("Add your Gemini API key before drafting emails.")
    if require_search and not tavily_key:
        raise RuntimeError("Add your Tavily API key before finding leads.")
    if require_search and not gemini_key:
        raise RuntimeError("Add your Gemini API key before extracting leads.")
    return Config(
        tavily_api_key=tavily_key or "",
        gemini_api_key=gemini_key or "",
        hunter_api_key=user.get("hunter_api_key") or None,
    )


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "429" in text and "quota" in text.lower():
        return (
            "Gemini quota is temporarily exhausted. Use the cached leads for now, "
            "or try fresh search again later."
        )
    if "Gmail API has not been used" in text or "accessNotConfigured" in text:
        return "Gmail API is not enabled for this OAuth project yet."
    if "invalid_grant" in text or "access_denied" in text:
        return "Google sign-in needs to be refreshed."
    return text.split("[links", 1)[0].strip()


def credentials_to_dict(creds: Credentials) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def load_web_credentials(email: str) -> Credentials | None:
    token_data = repo.get_gmail_token(email)
    if not token_data:
        return None
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        repo.save_gmail_token(email, user_key(), credentials_to_dict(creds))
    return creds if creds.valid else None


def google_flow() -> Flow:
    if not DEFAULT_CREDENTIALS_FILE.exists():
        raise FileNotFoundError("Missing credentials.json. Run setup-gmail first.")
    return Flow.from_client_secrets_file(
        str(DEFAULT_CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=url_for("oauth_callback", _external=True),
    )


def fetch_user_info(creds: Credentials) -> dict[str, Any]:
    response = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def gmail_service():
    email = current_user_email()
    if not email:
        raise RuntimeError("Please sign in with Google first.")
    creds = load_web_credentials(email)
    if not creds:
        raise RuntimeError("Please sign in with Google first.")
    return build("gmail", "v1", credentials=creds)


def signed_in_user() -> dict[str, Any] | None:
    return session.get("user")


def require_signed_in() -> str:
    """Returns the signed-in user's email, or raises if nobody is signed in."""
    email = current_user_email()
    if not email:
        raise RuntimeError("Sign in with Google first.")
    return email


def public_drafts() -> list[dict[str, Any]]:
    email = current_user_email()
    if not email:
        return []
    drafts = repo.list_drafts(email)
    visible = []
    for draft in drafts:
        if draft.get("status") == "pending_approval" and not valid_email(draft.get("to")):
            draft = {**draft, "status": "needs_contact"}
        visible.append({**draft, "resume_name": resume_display_name(draft.get("resume_path"))})
    return visible


def queue_context() -> dict[str, Any]:
    drafts = public_drafts()
    pending = [
        draft
        for draft in drafts
        if draft.get("status") == "pending_approval" and valid_email(draft.get("to"))
    ]
    needs_contact = [draft for draft in drafts if draft.get("status") == "needs_contact"]
    current = pending[0] if pending else None
    email = current_user_email()
    return {
        "drafts": drafts,
        "current_draft": current,
        "pending_count": len(pending),
        "needs_contact": needs_contact,
        "history": repo.history(email) if email else [],
    }


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    app.logger.exception(exc)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": friendly_error(exc)}), 500
    return render_template("error.html", error=exc), 500


@app.get("/")
def index():
    user = signed_in_user()
    signed_in = user is not None
    db_user = repo.get_user(current_user_email()) if signed_in else None
    resume_path = (db_user or {}).get("resume_path")
    context = (
        queue_context()
        if signed_in
        else {"drafts": [], "current_draft": None, "pending_count": 0, "needs_contact": [], "history": []}
    )
    return render_template(
        "index.html",
        signed_in=signed_in,
        user=user,
        resume_path=resume_path,
        resume_name=resume_display_name(resume_path),
        api_key_status=api_key_status(),
        internships_count=repo.count_opportunities(),
        contacts_count=repo.count_contacts(),
        drafts=context["drafts"],
        current_draft=context["current_draft"],
        pending_count=context["pending_count"],
        needs_contact=context["needs_contact"],
        history=context["history"],
    )


@app.get("/history")
def history_page():
    user = signed_in_user()
    if not user:
        return redirect(url_for("index"))
    db_user = repo.get_user(current_user_email()) or {}
    resume_path = db_user.get("resume_path")
    return render_template(
        "history.html",
        signed_in=True,
        user=user,
        resume_path=resume_path,
        resume_name=resume_display_name(resume_path),
        api_key_status=api_key_status(),
        history=repo.history(current_user_email()),
    )


@app.get("/stats")
def stats_page():
    user = signed_in_user()
    if not user:
        return redirect(url_for("index"))
    db_user = repo.get_user(current_user_email()) or {}
    return render_template(
        "stats.html",
        signed_in=True,
        user=user,
        resume_name=resume_display_name(db_user.get("resume_path")),
        api_key_status=api_key_status(),
        stats=aggregate_stats(db),
    )


@app.get("/auth/google")
def auth_google():
    flow = google_flow()
    authorization_url, oauth_state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = oauth_state
    return redirect(authorization_url)


@app.get("/oauth2callback")
def oauth_callback():
    flow = google_flow()
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    info = fetch_user_info(creds)
    email = info.get("email")
    if not email:
        raise RuntimeError("Google did not return an email address for this account.")
    session["user"] = info
    key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", email).strip("_") or "local"
    repo.save_gmail_token(email, key, credentials_to_dict(creds))
    return redirect(url_for("index"))


@app.post("/api/logout")
def logout():
    email = current_user_email()
    if email:
        repo.clear_gmail_token(email)
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/upload")
def upload_resume():
    require_signed_in()
    ensure_dirs()
    file = request.files.get("resume")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Choose a resume PDF first."}), 400
    if not file.filename.lower().endswith((".pdf", ".txt", ".md")):
        return jsonify({"ok": False, "error": "Upload a PDF, TXT, or MD resume."}), 400

    filename = secure_filename(file.filename)
    path = UPLOAD_DIR / filename
    file.save(path)
    repo.save_resume_path(current_user_email(), user_key(), str(path))
    return jsonify({"ok": True, "resume_path": str(path)})


@app.post("/api/settings")
def save_settings():
    require_signed_in()
    repo.save_api_keys(
        current_user_email(),
        user_key(),
        gemini_api_key=request.form.get("gemini_api_key", "").strip() or None,
        tavily_api_key=request.form.get("tavily_api_key", "").strip() or None,
        hunter_api_key=request.form.get("hunter_api_key", "").strip() or None,
    )
    return jsonify({"ok": True, "api_key_status": api_key_status()})


@app.post("/api/search")
def api_search():
    require_signed_in()
    limit = int(request.form.get("limit", 10))
    email = current_user_email()
    run_id = new_run_id()
    try:
        config = user_config(require_search=True)
        search_internships(limit, config, repo, run_id=run_id, user_email=email)
        find_contacts(repo.list_opportunities(), config, repo, run_id=run_id, user_email=email)
        warning = None
    except Exception as exc:
        if not repo.list_opportunities(limit):
            raise
        warning = friendly_error(exc)
    return jsonify(
        {
            "ok": True,
            "internships_count": repo.count_opportunities(),
            "contacts_count": repo.count_contacts(),
            "warning": warning,
        }
    )


@app.post("/api/draft")
def api_draft():
    require_signed_in()
    config = user_config(require_draft=True)
    email = current_user_email()
    db_user = repo.get_user(email) or {}
    resume_path = db_user.get("resume_path")
    if not resume_path:
        return jsonify({"ok": False, "error": "Upload a resume first."}), 400

    limit = int(request.form.get("limit", 10))
    contacts = repo.list_contacts()
    blocked_companies = repo.history_company_keys(email)
    existing_companies = repo.existing_draft_company_keys(email)
    candidates = [
        contact
        for contact in contacts
        if valid_email(contact.get("email"))
        if company_key(contact.get("company")) not in blocked_companies
        and company_key(contact.get("company")) not in existing_companies
    ][:limit]
    if not candidates:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "No sendable contacts found. Add a Hunter key, rerun Find leads, or use job source links directly.",
                    **queue_context(),
                }
            ),
            400,
        )

    created = draft_emails(Path(resume_path), candidates, limit, config, repo, email)
    for draft in created:
        repo.remember_company(email, draft, "drafted")
    return jsonify({"ok": True, **queue_context()})


@app.get("/api/drafts")
def api_drafts():
    require_signed_in()
    return jsonify({"ok": True, **queue_context()})


@app.get("/api/stats")
def api_stats():
    require_signed_in()
    return jsonify({"ok": True, "stats": aggregate_stats(db)})


@app.post("/api/drafts/<int:draft_id>/skip")
def api_skip(draft_id: int):
    email = require_signed_in()
    draft = repo.update_draft_status(email, draft_id, "removed")
    if draft is None:
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    repo.remember_company(email, draft, "removed")
    return jsonify({"ok": True, "draft": draft})


@app.post("/api/drafts/<int:draft_id>/remove")
def api_remove(draft_id: int):
    return api_skip(draft_id)


@app.post("/api/drafts/<int:draft_id>/send")
def api_send(draft_id: int):
    email = require_signed_in()
    draft = repo.get_draft(email, draft_id)
    if draft is None:
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    if not draft.get("to") or "@" not in draft["to"]:
        updated = repo.update_draft_status(email, draft_id, "missing_email")
        assert updated is not None
        repo.remember_company(email, updated, "missing_email")
        return jsonify({"ok": False, "error": "Draft has no valid recipient.", "draft": updated}), 400
    if draft.get("status") == "sent":
        return jsonify({"ok": True, "draft": draft})

    sent = send_message(gmail_service(), draft["to"], draft["subject"], draft["body"], draft.get("resume_path"))
    updated = repo.update_draft_status(email, draft_id, "sent", gmail_message_id=sent.get("id"))
    assert updated is not None
    repo.remember_company(email, updated, "sent")
    return jsonify({"ok": True, "draft": updated})


if __name__ == "__main__":
    ensure_dirs()
    app.run(host="127.0.0.1", port=5001, debug=True)
