from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    gemini_api_key: str = ""

    # Vector config
    vector_dimension: int = 768
    qdrant_collection: str = "omnibrain"
    qdrant_path: str = str(Path(__file__).parent.parent / "storage" / "qdrant")

    # Server
    host: str = "0.0.0.0"
    port: int = 5005

    # Search
    top_k: int = 5
    rerank_pool: int = 50

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()