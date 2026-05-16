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

# combine all the result text into one big blob
combined_text = ""
for r in search_results["results"]:
    combined_text += f"TITLE: {r['title']}\nURL: {r['url']}\nCONTENT: {r['content']}\n\n"

# step 2: ask gemini to extract company names from the blob
print("🧠 asking gemini to extract companies...")
prompt = f"""
Below are web search results about startups hiring summer 2026 interns.
Extract a clean list of company names that are explicitly mentioned as hiring interns.

For each company, give me:
- Company name
- One-line description (if available)
- The source URL where it was mentioned

Format your response as a numbered list. Only include companies you're confident about.

SEARCH RESULTS:
{combined_text}
"""

response = model.generate_content(prompt)
print("\n✅ companies found:\n")
print(response.text)