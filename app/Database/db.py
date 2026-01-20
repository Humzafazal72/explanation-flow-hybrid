import os
from libsql_client import create_client
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.environ.get("TURSO_EXPLANATION_DB_URL")
DB_TOKEN = os.environ.get("TURSO_EXPLANATION_DB_TOKEN")

sqlite_client = create_client(url=DB_URL, auth_token=DB_TOKEN)


async def get_db():
    yield sqlite_client
