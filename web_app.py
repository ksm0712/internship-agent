import json
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
    DEFAULT_CONTACTS_FILE,
    DEFAULT_CREDENTIALS_FILE,
    DEFAULT_DRAFTS_FILE,
    DEFAULT_SEARCH_FILE,
    ROOT,
    draft_emails,
    find_contacts,
    json_load,
    json_save,
    search_internships,
    send_message,
)


os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

UPLOAD_DIR = ROOT / "uploads"
WEB_TOKEN_FILE = ROOT / "data" / "web_google_token.json"
WEB_STATE_FILE = ROOT / "data" / "web_state.json"
HISTORY_DIR = ROOT / "data" / "history"
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "local-dev-change-me")
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.send",
]

app = Flask(__name__)
app.secret_key = SECRET_KEY


def ensure_web_dirs() -> None:
    UPLOAD_DIR.mkdir(exist_ok=True)
    WEB_TOKEN_FILE.parent.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)


def state() -> dict[str, Any]:
    return json_load(WEB_STATE_FILE, {})


def save_state(data: dict[str, Any]) -> None:
    json_save(WEB_STATE_FILE, data)


def current_user_email() -> str | None:
    user = session.get("user") or {}
    return user.get("email")


def user_key() -> str:
    email = current_user_email() or "local"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", email).strip("_") or "local"


def user_state() -> dict[str, Any]:
    data = state()
    users = data.setdefault("users", {})
    current = users.setdefault(user_key(), {})
    if not current.get("resume_path") and data.get("resume_path"):
        current["resume_path"] = data["resume_path"]
        save_state(data)
    return current


def save_user_state(user_data: dict[str, Any]) -> None:
    data = state()
    users = data.setdefault("users", {})
    users[user_key()] = user_data
    save_state(data)


def history_file() -> Path:
    ensure_web_dirs()
    return HISTORY_DIR / f"{user_key()}.json"


def user_drafts_file() -> Path:
    ensure_web_dirs()
    return ROOT / "data" / f"drafts_{user_key()}.json"


def history() -> list[dict[str, Any]]:
    items = json_load(history_file(), [])
    if items:
        return items
    legacy_drafts = json_load(DEFAULT_DRAFTS_FILE, [])
    for draft in legacy_drafts:
        if not draft.get("company"):
            continue
        items.append(
            {
                "company": draft.get("company"),
                "role": draft.get("role"),
                "to": draft.get("to", ""),
                "subject": draft.get("subject", ""),
                "status": draft.get("status", "drafted"),
                "gmail_message_id": draft.get("gmail_message_id"),
            }
        )
    if items:
        save_history(items)
    return items


def save_history(items: list[dict[str, Any]]) -> None:
    json_save(history_file(), items)


def company_key(company: str | None) -> str:
    return re.sub(r"\W+", "", (company or "").lower())


def history_company_keys() -> set[str]:
    return {company_key(item.get("company")) for item in history() if item.get("company")}


def remember_company(draft: dict[str, Any], status: str) -> None:
    items = history()
    key = company_key(draft.get("company"))
    for item in items:
        if company_key(item.get("company")) == key:
            item.update(
                {
                    "role": draft.get("role"),
                    "to": draft.get("to", ""),
                    "subject": draft.get("subject", ""),
                    "status": status,
                    "gmail_message_id": draft.get("gmail_message_id", item.get("gmail_message_id")),
                }
            )
            break
    else:
        items.append(
            {
                "company": draft.get("company"),
                "role": draft.get("role"),
                "to": draft.get("to", ""),
                "subject": draft.get("subject", ""),
                "status": status,
                "gmail_message_id": draft.get("gmail_message_id"),
            }
        )
    save_history(items)


def credentials_to_dict(creds: Credentials) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def load_web_credentials() -> Credentials | None:
    if not WEB_TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(WEB_TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        json_save(WEB_TOKEN_FILE, credentials_to_dict(creds))
    return creds if creds.valid else None


def google_flow() -> Flow:
    if not DEFAULT_CREDENTIALS_FILE.exists():
        raise FileNotFoundError("Missing credentials.json. Run setup-gmail first.")
    return Flow.from_client_secrets_file(
        str(DEFAULT_CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=url_for("oauth_callback", _external=True),
    )


def user_info(creds: Credentials) -> dict[str, Any]:
    response = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def gmail_service():
    creds = load_web_credentials()
    if not creds:
        raise RuntimeError("Please sign in with Google first.")
    return build("gmail", "v1", credentials=creds)


def signed_in_user() -> dict[str, Any] | None:
    creds = load_web_credentials()
    if not creds:
        return None
    if not session.get("user"):
        session["user"] = user_info(creds)
    return session.get("user")


def require_signed_in() -> None:
    if not signed_in_user():
        raise RuntimeError("Sign in with Google first.")


def public_drafts() -> list[dict[str, Any]]:
    drafts = json_load(user_drafts_file(), [])
    email = current_user_email()
    visible = []
    for index, draft in enumerate(drafts):
        draft_user = draft.get("user_email")
        if draft_user and email and draft_user != email:
            continue
        if draft.get("status") == "removed":
            continue
        visible.append({**draft, "index": index})
    return visible


def queue_context() -> dict[str, Any]:
    drafts = public_drafts()
    pending = [
        draft
        for draft in drafts
        if draft.get("status") in {"pending_approval", "missing_email"}
    ]
    current = pending[0] if pending else None
    return {
        "drafts": drafts,
        "current_draft": current,
        "pending_count": len(pending),
        "history": history(),
    }


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    app.logger.exception(exc)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": str(exc)}), 500
    return render_template("error.html", error=exc), 500


@app.get("/")
def index():
    user = signed_in_user()
    signed_in = user is not None
    user_data = user_state() if signed_in else {}
    context = queue_context() if signed_in else {"drafts": [], "current_draft": None, "pending_count": 0, "history": []}
    return render_template(
        "index.html",
        signed_in=signed_in,
        user=user,
        resume_path=user_data.get("resume_path"),
        internships_count=len(json_load(DEFAULT_SEARCH_FILE, [])),
        contacts_count=len(json_load(DEFAULT_CONTACTS_FILE, [])),
        drafts=context["drafts"],
        current_draft=context["current_draft"],
        pending_count=context["pending_count"],
        history=context["history"],
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
    ensure_web_dirs()
    json_save(WEB_TOKEN_FILE, credentials_to_dict(creds))
    session["user"] = user_info(creds)
    return redirect(url_for("index"))


@app.post("/api/logout")
def logout():
    session.clear()
    if WEB_TOKEN_FILE.exists():
        WEB_TOKEN_FILE.unlink()
    return jsonify({"ok": True})


@app.post("/api/upload")
def upload_resume():
    require_signed_in()
    ensure_web_dirs()
    file = request.files.get("resume")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Choose a resume PDF first."}), 400
    if not file.filename.lower().endswith((".pdf", ".txt", ".md")):
        return jsonify({"ok": False, "error": "Upload a PDF, TXT, or MD resume."}), 400

    filename = secure_filename(file.filename)
    path = UPLOAD_DIR / filename
    file.save(path)
    user_data = user_state()
    user_data["resume_path"] = str(path)
    save_user_state(user_data)
    return jsonify({"ok": True, "resume_path": str(path)})


@app.post("/api/search")
def api_search():
    require_signed_in()
    limit = int(request.form.get("limit", 10))
    internships = search_internships(limit)
    contacts = find_contacts(DEFAULT_SEARCH_FILE)
    return jsonify(
        {
            "ok": True,
            "internships_count": len(internships),
            "contacts_count": len(contacts),
        }
    )


@app.post("/api/draft")
def api_draft():
    require_signed_in()
    user_data = user_state()
    resume_path = user_data.get("resume_path")
    if not resume_path:
        return jsonify({"ok": False, "error": "Upload a resume first."}), 400

    limit = int(request.form.get("limit", 10))
    contacts = json_load(DEFAULT_CONTACTS_FILE, [])
    blocked_companies = history_company_keys()
    existing_companies = {
        company_key(draft.get("company"))
        for draft in public_drafts()
        if draft.get("status") != "removed"
    }
    candidates = [
        contact
        for contact in contacts
        if company_key(contact.get("company")) not in blocked_companies
        and company_key(contact.get("company")) not in existing_companies
    ][:limit]
    if not candidates:
        return jsonify(
            {
                "ok": False,
                "error": "No new companies to draft. Your history already has these companies.",
                **queue_context(),
            }
        ), 400

    temp_contacts = ROOT / "data" / f"web_contacts_{user_key()}.json"
    drafts_file = user_drafts_file()
    before_count = len(json_load(drafts_file, []))
    json_save(temp_contacts, candidates)
    draft_emails(Path(resume_path), temp_contacts, limit, drafts_file)

    all_drafts = json_load(drafts_file, [])
    for draft in all_drafts[before_count:]:
        draft["user_email"] = current_user_email()
        draft["resume_path"] = resume_path
        remember_company(draft, "drafted")
    json_save(drafts_file, all_drafts)
    return jsonify({"ok": True, **queue_context()})


@app.get("/api/drafts")
def api_drafts():
    require_signed_in()
    return jsonify({"ok": True, **queue_context()})


@app.post("/api/drafts/<int:index>/skip")
def api_skip(index: int):
    require_signed_in()
    drafts = json_load(user_drafts_file(), [])
    if index < 0 or index >= len(drafts):
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    drafts[index]["status"] = "removed"
    remember_company(drafts[index], "removed")
    json_save(user_drafts_file(), drafts)
    return jsonify({"ok": True, "draft": {**drafts[index], "index": index}})


@app.post("/api/drafts/<int:index>/remove")
def api_remove(index: int):
    return api_skip(index)


@app.post("/api/drafts/<int:index>/send")
def api_send(index: int):
    require_signed_in()
    drafts = json_load(user_drafts_file(), [])
    if index < 0 or index >= len(drafts):
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    draft = drafts[index]
    if not draft.get("to") or "@" not in draft["to"]:
        draft["status"] = "missing_email"
        remember_company(draft, "missing_email")
        json_save(user_drafts_file(), drafts)
        return jsonify({"ok": False, "error": "Draft has no valid recipient.", "draft": draft}), 400
    if draft.get("status") == "sent":
        return jsonify({"ok": True, "draft": {**draft, "index": index}})

    sent = send_message(
        gmail_service(),
        draft["to"],
        draft["subject"],
        draft["body"],
        draft.get("resume_path"),
    )
    draft["status"] = "sent"
    draft["gmail_message_id"] = sent.get("id")
    remember_company(draft, "sent")
    json_save(user_drafts_file(), drafts)
    return jsonify({"ok": True, "draft": {**draft, "index": index}})


if __name__ == "__main__":
    ensure_web_dirs()
    app.run(host="127.0.0.1", port=5001, debug=True)
