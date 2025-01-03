import os
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    "openai_api_key": os.getenv("OPENAI_API_KEY"),
    "openai_endpoint_url": "https://api.openai.com/v1",
    "wcd_url": os.getenv("WCD_URL"),
    "wcd_api_key": os.getenv("WCD_API_KEY"),
    "embed_model": "text-embedding-3-large",
    "vector_store_name": "Papers",
    "openai_client_kwargs": {
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 500,
    },
    "database_path": "papers.db",
}
