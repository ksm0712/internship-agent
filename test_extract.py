import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# extract full content from a single URL
url = "https://www.ycombinator.com/internships"
result = tavily.extract(urls=[url])

# print what we got back
for page in result["results"]:
    print(f"URL: {page['url']}")
    print(f"CONTENT (first 1000 chars):\n{page['raw_content'][:1000]}")
    print("---")