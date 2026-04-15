"""
app/config.py
Central configuration — app settings + LangSmith observability settings.
"""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Anthropic (used by langchain-anthropic under the hood)
    anthropic_api_key: str = ""

    # LangSmith — set LANGCHAIN_TRACING_V2=true to enable auto-tracing
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "examatlas"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # LangChain model
    llm_model: str = "claude-sonnet-4-20250514"

    # BFF secret — the proxy injects X-BFF-Key with this value;
    # the BFFAuthMiddleware rejects requests that don't match.
    # Leave blank in dev to run without the BFF.
    bff_secret_key: str = ""

    # Redis (leave blank to disable)
    redis_url: str = ""   # e.g. redis://localhost:6379/0

    # Logging
    log_level: str = ""
    log_file: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def configure_langsmith(self) -> None:
        """
        LangSmith reads from env vars directly.
        Call this at app startup to ensure vars are set before any chain runs.
        """
        if self.langchain_tracing_v2 and self.langchain_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_API_KEY"] = self.langchain_api_key
            os.environ["LANGCHAIN_PROJECT"] = self.langchain_project
            os.environ["LANGCHAIN_ENDPOINT"] = self.langchain_endpoint
        # Anthropic key must be in env for langchain-anthropic
        if self.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = self.anthropic_api_key

@lru_cache
def get_settings() -> Settings:
    return Settings()
