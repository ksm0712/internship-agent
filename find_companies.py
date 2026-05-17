import os
from dotenv import load_dotenv
from tavily import TavilyClient
import google.generativeai as genai

load_dotenv()

# setup both clients
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash-lite")

# step 1: run multiple targeted searches
print("🔎 running multi-query search...")
queries = [
    "Singapore AI startups hiring interns 2026",
    "AI ML internships Singapore startup",
    "remote AI startup internship summer 2026",
    "Southeast Asia AI startups hiring engineering interns",
]

all_results = []
for q in queries:
    print(f"   → {q}")
    res = tavily.search(query=q, max_results=4)
    all_results.extend(res["results"])

# deduplicate by URL (some results overlap across queries)
seen = set()
unique_results = []
for r in all_results:
    if r["url"] not in seen:
        seen.add(r["url"])
        unique_results.append(r)

print(f"   ✅ {len(unique_results)} unique results")
search_results = {"results": unique_results}

# step 1.5: extract full content from each result url
print("📄 extracting full page content from each result...")
urls_to_extract = [r["url"] for r in search_results["results"]]
extracted = tavily.extract(urls=urls_to_extract)

# combine all extracted page text into one big blob
combined_text = ""
for page in extracted["results"]:
    combined_text += f"URL: {page['url']}\nCONTENT: {page['raw_content'][:5000]}\n\n"

# step 2: ask gemini to extract company names from the blob
print("🧠 asking gemini to extract companies...")
prompt = f"""
Below are web search results about AI/ML startups hiring interns (Singapore-based or remote-friendly).

Extract EVERY startup mentioned as hiring interns or with open intern roles. Focus on AI/ML companies, but include other tech startups if they're hiring AI/ML interns.

EXCLUDE: accelerators, VCs, job boards, newsletters, universities (e.g. Y Combinator, Wellfound, e27 itself, Tech in Asia, NUS).

For each company, extract its official website URL ONLY if you can clearly see it in the content. If not visible, set official_url to null.

Respond ONLY with valid JSON, no markdown:
[
  {{
    "name": "Company Name",
    "description": "What they do + the intern role if mentioned",
    "location": "City/country if mentioned, else null",
    "source_url": "URL where mentioned",
    "official_url": "Official site URL if visible, else null"
  }}
]

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