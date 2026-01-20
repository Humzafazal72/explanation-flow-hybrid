import os
from dotenv import load_dotenv
from openai import AsyncOpenAI
from google.genai import Client

load_dotenv()

async_openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
google_client = Client()
