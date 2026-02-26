from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from routes import explanation_route

app = FastAPI()

app.include_router(explanation_route.router, prefix="", tags=["Agents"])

@app.get("/")
async def health_check():
    return {"data":"Working", "status":200}