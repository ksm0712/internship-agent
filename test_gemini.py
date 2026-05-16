import os
from dotenv import load_dotenv
import google.generativeai as genai

# load API keys from .env
load_dotenv()

# configure gemini with your key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# create a model instance
model = genai.GenerativeModel("gemini-2.5-flash")

# send a prompt and print the response
response = model.generate_content("Say hi in a fun way, one sentence.")
print(response.text)