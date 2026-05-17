import os
import json
import requests
from dotenv import load_dotenv
from tavily import TavilyClient
import google.generativeai as genai
import time

load_dotenv()

tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash-lite")
HUNTER_KEY = os.getenv("HUNTER_API_KEY")

# load companies from the previous step
with open("companies.json", "r") as f:
    companies = json.load(f)

print(f"📋 loaded {len(companies)} companies\n")

# load existing contacts so we don't redo work
try:
    with open("contacts.json", "r") as f:
        contacts = json.load(f)
    done_companies = {c["company"] for c in contacts}
    print(f"📂 already have {len(contacts)} contacts, will skip those")
except FileNotFoundError:
    contacts = []
    done_companies = set()


def find_domain(company_name):
    """Use Tavily search to find the company's official website domain."""
    results = tavily.search(query=f"{company_name} startup official website", max_results=3)
    urls = [r["url"] for r in results["results"]]

    # ask gemini to pick the most likely official domain
    prompt = f"""
    From these URLs, what is the official website domain for the company "{company_name}"?
    URLs: {urls}

    Respond with ONLY the bare domain (like "example.com"), no protocol, no path, no commentary.
    If you cannot confidently identify the official domain, respond with "UNKNOWN".
    """
    response = model.generate_content(prompt)
    return response.text.strip()


def find_contacts(domain):
    """Use Hunter.io to get emails for a domain."""
    url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_KEY}"
    res = requests.get(url)
    if res.status_code != 200:
        return []
    data = res.json().get("data", {})
    return data.get("emails", [])


# loop through each company
for c in companies:
    if c["name"] in done_companies:
        print(f"⏭️  skipping {c['name']} (already have contact)")
        continue
    print(f"🔎 looking up {c['name']}...")

    domain = find_domain(c["name"])
    if domain == "UNKNOWN" or "." not in domain:
        print(f"   ⚠️  couldn't find domain, skipping")
        continue

    print(f"   🌐 domain: {domain}")
    emails = find_contacts(domain)

    if not emails:
        print(f"   ⚠️  no emails found")
        continue

    # filter for founders / engineers / leadership
    good = [e for e in emails if e.get("position") and any(
        kw in e["position"].lower() for kw in ["founder", "ceo", "cto", "engineer", "head"]
    )]

    chosen = good[0] if good else emails[0]
    print(f"   ✅ {chosen.get('first_name', '')} {chosen.get('last_name', '')} — {chosen.get('position', 'unknown')} — {chosen['value']}")

    contacts.append({
        "company": c["name"],
        "description": c["description"],
        "domain": domain,
        "name": f"{chosen.get('first_name', '')} {chosen.get('last_name', '')}".strip(),
        "position": chosen.get("position", ""),
        "email": chosen["value"],
    })
    time.sleep(13)  # stay under gemini free tier rate limit (5 reqs/min)

# save contacts
with open("contacts.json", "w") as f:
    json.dump(contacts, f, indent=2)

print(f"\n💾 saved {len(contacts)} contacts to contacts.json")