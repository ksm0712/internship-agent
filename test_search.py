import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

# create tavily client with your api key
client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# search the web
results = client.search(
    query="YC startups hiring summer 2026 interns",
    max_results=5
)

# print each result
for r in results["results"]:
    print(r["title"])
    print(r["url"])
    print(r["content"][:200])  # first 200 chars
    print("---")