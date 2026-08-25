"""Gmail send + CLI OAuth helpers. The web app has its own OAuth flow
(web_app.py); this module covers the CLI's local `InstalledAppFlow` path and
the `send_message` call both paths share.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import shutil
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from internship_agent.retry import retry_with_backoff

GMAIL_SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def gmail_service(credentials_file: Path, token_file: Path) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SEND_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Missing {credentials_file}. Download an OAuth desktop client JSON "
                    "from Google Cloud and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), GMAIL_SEND_SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


@retry_with_backoff(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
def send_message(
    service: Any,
    to_email: str,
    subject: str,
    body: str,
    attachment_path: str | None = None,
) -> dict[str, Any]:
    message = EmailMessage()
    message.set_content(body)
    message["To"] = to_email
    message["Subject"] = subject
    if attachment_path:
        path = Path(attachment_path).expanduser()
        if path.exists():
            mime_type, _ = mimetypes.guess_type(path)
            maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": encoded}).execute()


def setup_gmail(credentials_file: Path) -> None:
    print("Gmail setup needs the OAuth Desktop Client JSON downloaded from Google Cloud.")
    print(f"It will be saved to {credentials_file}")
    source = input("Path to downloaded OAuth JSON file: ").strip()
    if not source:
        raise RuntimeError("No credentials file path provided.")

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"File not found: {source_path}")

    with source_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "installed" not in data and "web" not in data:
        raise RuntimeError("That file does not look like a Google OAuth client JSON.")

    credentials_file.parent.mkdir(exist_ok=True)
    shutil.copyfile(source_path, credentials_file)
    print(f"Saved Gmail OAuth credentials to {credentials_file}")
    print("This file is ignored by git.")
