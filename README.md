# Internship Agent

An AI agent that finds startups hiring summer interns, drafts personalized cold emails using my resume, and lets me approve before sending.

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
pip install -r requirements.txt
```

For Gmail sending, create an OAuth Desktop Client in Google Cloud with Gmail API enabled, download the JSON, then run:

```bash
python internship_agent.py setup-gmail
```

It asks for the downloaded JSON path and saves it as `credentials.json`. The first send run opens a browser sign-in and creates `token.json`. Both files are ignored by git.

## Run everything

Use a text, markdown, or text-extractable PDF resume. If you omit `--resume`, the script prompts you for the path:

```bash
python internship_agent.py run --resume /absolute/path/to/resume.pdf --limit 15
```

The final step previews each email:

```text
Send this email? [y]es / [n]o skip / [q]uit:
```

No email is sent unless you type `y` for that exact draft.

## Run step by step

```bash
python internship_agent.py search --limit 25
python internship_agent.py contacts
python internship_agent.py draft --resume /absolute/path/to/resume.pdf --limit 25
python internship_agent.py send
```

Useful output files:

- `data/internships.json`
- `data/contacts.json`
- `out/email_drafts.json`
