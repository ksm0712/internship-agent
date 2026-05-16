import os
from dotenv import load_dotenv
from tavily import TavilyClient
import google.generativeai as genai

load_dotenv()

# setup both clients
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash")

# step 1: search the web
print("🔎 searching the web...")
search_results = tavily.search(
    query="YC startups hiring summer 2026 software engineering interns",
    max_results=5
)

# step 1.5: extract full content from each result url
print("📄 extracting full page content from each result...")
urls_to_extract = [r["url"] for r in search_results["results"]]
extracted = tavily.extract(urls=urls_to_extract)

# combine all extracted page text into one big blob
combined_text = ""
for page in extracted["results"]:
    combined_text += f"URL: {page['url']}\nCONTENT: {page['raw_content'][:3000]}\n\n"

# step 2: ask gemini to extract company names from the blob
print("🧠 asking gemini to extract companies...")
prompt = f"""
Below are web search results about startups hiring summer 2026 interns.
Extract a clean list of companies explicitly mentioned as hiring interns.

Respond ONLY with valid JSON in this exact format, no other text:
[
  {{
    "name": "Company Name",
    "description": "One-line description",
    "source_url": "URL where mentioned"
  }}
]

Only include companies you're confident about. No markdown, no commentary, just JSON.

SEARCH RESULTS:
{combined_text}
"""

import json

response = model.generate_content(prompt)

# clean up — sometimes gemini wraps json in ```json ... ``` markdown
raw = response.text.strip()
if raw.startswith("```"):
    raw = raw.split("```")[1]  # take whats between the fences
    if raw.startswith("json"):
        raw = raw[4:]  # remove "json" label
    raw = raw.strip()

# parse it into actual python data
companies = json.loads(raw)

print(f"\n✅ found {len(companies)} companies:\n")
for c in companies:
    print(f"- {c['name']}: {c['description']}")
    print(f"  ↳ {c['source_url']}\n")

# save companies to a file for the next step
with open("companies.json", "w") as f:
    json.dump(companies, f, indent=2)

print(f"\n💾 saved to companies.json")