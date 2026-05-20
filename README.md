# Internship Agent

An AI agent that finds startups hiring summer interns, drafts personalized cold emails using my resume, and lets me approve before sending.

Status: working end-to-end. It has successfully sent Gmail messages with the resume PDF attached after terminal approval.

## Stack
- Python
- Gemini API (LLM brain)
- Tavily (web search)
- Hunter.io (finding emails)
- Gmail API (sending emails)

## What it does

`internship_agent.py` runs the full workflow:

1. Searches the web for current AI/tech/CS internships in Singapore.
2. Extracts and deduplicates real internship opportunities.
3. Finds a likely contact email with Hunter.io, or falls back to a generic `careers@domain`.
4. Drafts a personalized email from your resume and the company's role details.
5. Shows every email in the terminal and sends only after you approve it.
6. Attaches your resume PDF when sending if the draft was created with a resume path.

Generated files are saved under `data/` and `out/` so you can inspect or rerun each step.

## Setup

Create `.env` with:

```bash
GEMINI_API_KEY=...
TAVILY_API_KEY=...
HUNTER_API_KEY=...
```

Install dependencies:

```bash
venv/bin/pip install -r requirements.txt
```

For Gmail sending:

1. Create an OAuth Desktop Client in Google Cloud.
2. Enable the Gmail API for the same Google Cloud project.
3. Download the OAuth client JSON.
4. Save it locally with:

```bash
venv/bin/python internship_agent.py setup-gmail
```

It asks for the downloaded JSON path and saves it as `credentials.json`. The first approved send opens a browser sign-in and creates `token.json`. Both files are ignored by git.

## Run everything

Use a text, markdown, or text-extractable PDF resume. If you omit `--resume`, the script prompts you for the path:

```bash
venv/bin/python internship_agent.py run --resume /absolute/path/to/resume.pdf --limit 15
```

The final step previews each email:

```text
Send this email? [y]es / [n]o skip / [q]uit:
```

No email is sent unless you type `y` for that exact draft.

## Run step by step

```bash
venv/bin/python internship_agent.py search --limit 25
venv/bin/python internship_agent.py contacts
venv/bin/python internship_agent.py draft --resume /absolute/path/to/resume.pdf --limit 25
venv/bin/python internship_agent.py send
```

## Web app test

The `web-app-test` branch includes a local Flask web app for the same workflow:

```bash
venv/bin/python web_app.py
```

Open:

```text
http://127.0.0.1:5001
```

In the web app you can:

- Sign in with Google for Gmail send permission.
- Add your own Gemini, Tavily, and optional Hunter API keys.
- Upload a resume PDF.
- Find internship leads and contacts.
- Generate email drafts.
- Review each draft in the browser and click `Send` or `Skip`.

The web app is designed as a bring-your-own-keys app. Each signed-in user supplies their own free-tier API keys, so app usage does not run on the developer's Gemini/Tavily/Hunter quota.

The web app still uses `credentials.json`, so create it first with:

```bash
venv/bin/python internship_agent.py setup-gmail
```

For local Google OAuth, add this redirect URI to the OAuth client if Google rejects login:

```text
http://127.0.0.1:5001/oauth2callback
```

Useful output files:

- `data/internships.json`
- `data/contacts.json`
- `out/email_drafts.json`

## Sending behavior

The send step prints each draft with recipient, subject, body, and attachment path. Type:

- `y` to send that email
- `n` to skip it
- `q` to quit without reviewing the rest

After a successful Gmail API response, the draft status is updated to `sent` and a `gmail_message_id` is saved in `out/email_drafts.json`.

Drafts without a valid `to` email are marked `missing_email` and skipped.

## Troubleshooting

If Gmail returns `403 Gmail API has not been used... or it is disabled`, enable Gmail API in the Google Cloud project that owns `credentials.json`, wait a few minutes, then rerun:

```bash
venv/bin/python internship_agent.py send
```

If OAuth blocks the login, add your Gmail account as a test user in the OAuth consent screen.

If Gemini quota is hit while drafting, the agent saves progress after each draft and falls back to a local template so the workflow can still continue.
