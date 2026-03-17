from pydantic_settings import BaseSettings
from pathlib import Path
from dotenv import load_dotenv
import os

# 🔥 ALWAYS resolve absolute project root
ROOT_DIR = Path(__file__).resolve().parent.parent  # /omnisearch

# 🔥 FORCE load .env (works with Tauri, PyInstaller, CLI)
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseSettings):
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Vector config
    vector_dimension: int = 768
    qdrant_collection: str = "omnibrain"
    qdrant_path: str = str(ROOT_DIR / "storage" / "qdrant")
    bm25_index_path: str = str(ROOT_DIR / "storage" / "bm25_index.pkl")

    # Server
    host: str = "0.0.0.0"
    port: int = 5005

    # Search pipeline
    top_k: int = 5
    rerank_pool: int = 50
    bm25_weight: float = 0.3
    vector_weight: float = 0.7

    # Reranker
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_enabled: bool = True
    reranker_device: str = "cpu"

    # Copilot / RAG
    gemini_llm_model: str = "gemini-2.0-flash"
    copilot_context_docs: int = 5
    copilot_max_context_chars: int = 12000

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket_name: str = "omnisearch"

    enable_cloud_sync: bool = False
    sync_on_shutdown: bool = True
    sync_user_id: str = "default"


settings = Settings()