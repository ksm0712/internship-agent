import json
import os
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


def state() -> dict[str, Any]:
    return json_load(WEB_STATE_FILE, {})


def save_state(data: dict[str, Any]) -> None:
    json_save(WEB_STATE_FILE, data)


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


def public_drafts() -> list[dict[str, Any]]:
    drafts = json_load(DEFAULT_DRAFTS_FILE, [])
    return [{**draft, "index": index} for index, draft in enumerate(drafts)]


@app.get("/")
def index():
    current_state = state()
    signed_in = load_web_credentials() is not None
    return render_template(
        "index.html",
        signed_in=signed_in,
        user=session.get("user"),
        resume_path=current_state.get("resume_path"),
        internships_count=len(json_load(DEFAULT_SEARCH_FILE, [])),
        contacts_count=len(json_load(DEFAULT_CONTACTS_FILE, [])),
        drafts=public_drafts(),
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
    ensure_web_dirs()
    file = request.files.get("resume")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Choose a resume PDF first."}), 400
    if not file.filename.lower().endswith((".pdf", ".txt", ".md")):
        return jsonify({"ok": False, "error": "Upload a PDF, TXT, or MD resume."}), 400

    filename = secure_filename(file.filename)
    path = UPLOAD_DIR / filename
    file.save(path)
    current_state = state()
    current_state["resume_path"] = str(path)
    save_state(current_state)
    return jsonify({"ok": True, "resume_path": str(path)})


@app.post("/api/search")
def api_search():
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
    current_state = state()
    resume_path = current_state.get("resume_path")
    if not resume_path:
        return jsonify({"ok": False, "error": "Upload a resume first."}), 400
    if DEFAULT_DRAFTS_FILE.exists() and request.form.get("reset", "true") == "true":
        DEFAULT_DRAFTS_FILE.unlink()
    limit = int(request.form.get("limit", 10))
    drafts = draft_emails(Path(resume_path), DEFAULT_CONTACTS_FILE, limit)
    return jsonify({"ok": True, "drafts_count": len(drafts), "drafts": public_drafts()})


@app.get("/api/drafts")
def api_drafts():
    return jsonify({"ok": True, "drafts": public_drafts()})


@app.post("/api/drafts/<int:index>/skip")
def api_skip(index: int):
    drafts = json_load(DEFAULT_DRAFTS_FILE, [])
    if index < 0 or index >= len(drafts):
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    drafts[index]["status"] = "skipped"
    json_save(DEFAULT_DRAFTS_FILE, drafts)
    return jsonify({"ok": True, "draft": {**drafts[index], "index": index}})


@app.post("/api/drafts/<int:index>/send")
def api_send(index: int):
    drafts = json_load(DEFAULT_DRAFTS_FILE, [])
    if index < 0 or index >= len(drafts):
        return jsonify({"ok": False, "error": "Draft not found."}), 404
    draft = drafts[index]
    if not draft.get("to") or "@" not in draft["to"]:
        draft["status"] = "missing_email"
        json_save(DEFAULT_DRAFTS_FILE, drafts)
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
    json_save(DEFAULT_DRAFTS_FILE, drafts)
    return jsonify({"ok": True, "draft": {**draft, "index": index}})


if __name__ == "__main__":
    ensure_web_dirs()
    app.run(host="127.0.0.1", port=5001, debug=True)
