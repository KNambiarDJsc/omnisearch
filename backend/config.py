from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    gemini_api_key: str = ""

    # Vector config
    vector_dimension: int = 768
    qdrant_collection: str = "omnibrain"
    qdrant_path: str = str(Path(__file__).parent.parent / "storage" / "qdrant")
    bm25_index_path: str = str(Path(__file__).parent.parent / "storage" / "bm25_index.pkl")

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

    # ── Cloudflare R2 ────────────────────────────────────────────
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket_name: str = "omnisearch"

    # Cloud sync behaviour
    enable_cloud_sync: bool = False
    sync_on_shutdown: bool = True       # auto-push on clean exit
    sync_user_id: str = "default"       # user namespace in R2

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()