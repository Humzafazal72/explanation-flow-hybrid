from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from api import explanation_route

app = FastAPI()

app.include_router(explanation_route.router, prefix="", tags=["Agents"])