"""
Centralized configuration for agent-nexus.

Uses Pydantic Settings to load all configuration from environment variables
with sensible defaults for the free-tier serverless deployment architecture.

Design Pattern: Singleton — a single Settings instance is created and reused
across the entire application via get_settings().
"""

from __future__ import annotations

import enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class Environment(str, enum.Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProviderType(str, enum.Enum):
    """Supported LLM provider backends."""

    GROQ = "groq"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    VLLM = "vllm"
    HUGGINGFACE = "huggingface"


class VectorDBProvider(str, enum.Enum):
    """Supported vector database providers."""

    QDRANT = "qdrant"
    CHROMA = "chroma"


# ---------------------------------------------------------------------------
# Settings Classes (grouped by concern)
# ---------------------------------------------------------------------------
class DatabaseSettings(BaseSettings):
    """PostgreSQL / Neon database configuration."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_nexus",
        description="Async SQLAlchemy database URL. Use Neon.tech URL for production.",
    )
    pool_size: int = Field(default=5, ge=1, le=20, description="Connection pool size.")
    pool_pre_ping: bool = Field(
        default=True, description="Ping connections before use (critical for Neon serverless)."
    )
    echo: bool = Field(default=False, description="Echo SQL queries to logs.")
    auto_migrate: bool = Field(
        default=True, description="Auto-create tables on startup."
    )


class RedisSettings(BaseSettings):
    """Upstash Redis configuration."""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(
        default="",
        description="Upstash Redis REST URL. Leave empty to use in-memory fallback.",
    )
    token: SecretStr = Field(
        default=SecretStr(""),
        description="Upstash Redis REST token.",
    )
    enabled: bool = Field(
        default=True,
        description="Enable Redis. If False or URL empty, uses in-memory cache.",
    )


class VectorDBSettings(BaseSettings):
    """Qdrant Cloud vector database configuration."""

    model_config = SettingsConfigDict(env_prefix="VECTOR_")

    provider: VectorDBProvider = Field(
        default=VectorDBProvider.QDRANT,
        description="Vector database provider.",
    )
    url: str = Field(
        default="http://localhost:6333",
        description="Qdrant Cloud URL or local Qdrant URL.",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Qdrant Cloud API key. Leave empty for local.",
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model for embeddings.",
    )
    embedding_dimension: int = Field(
        default=384,
        description="Embedding vector dimension (must match model).",
    )


class LLMSettings(BaseSettings):
    """LLM provider configuration (defaults to Groq free tier)."""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    provider: LLMProviderType = Field(
        default=LLMProviderType.GROQ,
        description="Default LLM provider.",
    )
    model: str = Field(
        default="groq/llama-3.3-70b-versatile",
        description="Default model name (litellm format: provider/model).",
    )
    vision_model: str = Field(
        default="groq/llama-3.2-90b-vision-preview",
        description="Vision-capable model for image understanding.",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key for the chosen provider (GROQ_API_KEY, OPENAI_API_KEY, etc.).",
    )
    temperature: float = Field(
        default=0.1, ge=0.0, le=2.0, description="Default temperature."
    )
    max_tokens: int = Field(
        default=4096, ge=1, le=128000, description="Default max output tokens."
    )
    max_retries: int = Field(
        default=3, ge=1, le=10, description="Max retries on LLM failure."
    )
    timeout: int = Field(
        default=120, ge=10, le=600, description="Timeout in seconds per LLM call."
    )
    fallback_model: str = Field(
        default="groq/llama-3.1-8b-instant",
        description="Fallback model if primary fails.",
    )

    # --- Provider-specific keys (litellm reads these from env) ---
    groq_api_key: SecretStr = Field(default=SecretStr(""), alias="GROQ_API_KEY")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")


class MCPSettings(BaseSettings):
    """MCP (Model Context Protocol) server configuration."""

    model_config = SettingsConfigDict(env_prefix="MCP_")

    workspace_dir: str = Field(
        default="/tmp/agent-nexus-workspace",
        description="Sandboxed workspace directory for filesystem/shell servers.",
    )
    shell_timeout: int = Field(
        default=30, ge=5, le=300, description="Shell command timeout in seconds."
    )
    browser_enabled: bool = Field(
        default=False,
        description="Enable Playwright browser server (requires ~400MB RAM).",
    )
    search_provider: str = Field(
        default="duckduckgo",
        description="Search provider: 'tavily' or 'duckduckgo'.",
    )
    tavily_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Tavily API key (free tier: 1000 searches/mo).",
    )


class MonitoringSettings(BaseSettings):
    """MLflow / DagsHub monitoring configuration."""

    model_config = SettingsConfigDict(env_prefix="MONITORING_")

    enabled: bool = Field(default=True, description="Enable metrics collection.")
    mlflow_tracking_uri: str = Field(
        default="",
        description="DagsHub MLflow tracking URI. Leave empty to skip MLflow.",
    )
    mlflow_experiment_name: str = Field(
        default="agent-nexus",
        description="MLflow experiment name.",
    )
    dagshub_token: SecretStr = Field(
        default=SecretStr(""),
        description="DagsHub API token for MLflow authentication.",
    )


class APISettings(BaseSettings):
    """FastAPI application settings."""

    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = Field(default="0.0.0.0", description="API host.")
    port: int = Field(default=7860, description="API port (7860 for HF Spaces).")
    debug: bool = Field(default=False, description="Enable debug mode.")
    cors_origins: list[str] = Field(
        default=["*"], description="Allowed CORS origins."
    )
    rate_limit: str = Field(
        default="60/minute",
        description="Global rate limit (slowapi format).",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Optional API key for authentication. Leave empty to disable.",
    )


# ---------------------------------------------------------------------------
# Root Settings (aggregates all sub-settings)
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """
    Root configuration for agent-nexus.

    All settings are loaded from environment variables with optional .env file support.
    Each sub-section has its own prefix (DB_, REDIS_, VECTOR_, LLM_, MCP_, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = Field(default="agent-nexus", description="Application name.")
    environment: Environment = Field(
        default=Environment.DEVELOPMENT, description="Application environment."
    )
    log_level: str = Field(
        default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR."
    )

    # --- Sub-settings (loaded independently) ---
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    vector_db: VectorDBSettings = Field(default_factory=VectorDBSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    api: APISettings = Field(default_factory=APISettings)

    @model_validator(mode="after")
    def _set_litellm_env_vars(self) -> "Settings":
        """Push API keys into environment so litellm can find them."""
        import os

        if self.llm.groq_api_key.get_secret_value():
            os.environ.setdefault("GROQ_API_KEY", self.llm.groq_api_key.get_secret_value())
        if self.llm.openai_api_key.get_secret_value():
            os.environ.setdefault("OPENAI_API_KEY", self.llm.openai_api_key.get_secret_value())
        if self.llm.anthropic_api_key.get_secret_value():
            os.environ.setdefault(
                "ANTHROPIC_API_KEY", self.llm.anthropic_api_key.get_secret_value()
            )
        if self.llm.ollama_host:
            os.environ.setdefault("OLLAMA_API_BASE", self.llm.ollama_host)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get the singleton Settings instance.

    Uses lru_cache to ensure only one instance is created.
    All config is loaded from environment variables + .env file.
    """
    return Settings()
