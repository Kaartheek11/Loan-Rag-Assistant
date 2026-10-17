"""Application configuration, read only from environment variables (section 15).

A local `.env` file is read as a development convenience; real environment variables
always take precedence over it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: Literal["development", "production"] = "development"

    database_url: str = "postgresql+psycopg://loanrag_app:loanrag@localhost:5432/loanrag"
    migration_database_url: str | None = None
    app_db_role: str = "loanrag_app"

    jwks_url: str | None = None
    jwt_public_key_path: str | None = None
    jwt_private_key_path: str = "./keys/test_private.pem"
    jwt_issuer: str = "http://localhost:8080/realms/loanrag"
    jwt_audience: str = "loanrag-api"

    keycloak_admin_password: SecretStr | None = None
    audit_hmac_key: SecretStr | None = None

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    faiss_index_path: str = "./data/index.faiss"
    document_store_path: str = "./data/documents"

    llm_provider: Literal["ollama", "hosted", "fake"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    hosted_llm_base_url: str = "https://api.groq.com/openai/v1"
    hosted_llm_model: str | None = None
    hosted_llm_api_key: SecretStr | None = None

    eval_requests_per_minute: int = Field(default=20, ge=1)
    retrieval_n_start: int = Field(default=50, ge=1)
    retrieval_n_max: int = Field(default=800, ge=1)
    top_k_default: int = Field(default=6, ge=1, le=10)
    rate_limit_per_minute: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.retrieval_n_max < self.retrieval_n_start:
            raise ValueError("RETRIEVAL_N_MAX must be >= RETRIEVAL_N_START")
        if not self.jwks_url and not self.jwt_public_key_path:
            raise ValueError("Set JWKS_URL (Keycloak) or JWT_PUBLIC_KEY_PATH (tests/dev)")
        return self

    @property
    def migrations_url(self) -> str:
        return self.migration_database_url or self.database_url

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs (S2)."""
        values: list[str] = []
        for secret in (self.audit_hmac_key, self.hosted_llm_api_key, self.keycloak_admin_password):
            if secret is not None and secret.get_secret_value():
                values.append(secret.get_secret_value())
        for url in (self.database_url, self.migration_database_url):
            password = _password_from_url(url)
            if password:
                values.append(password)
        return values

    def validate_for_startup(self) -> None:
        """Refuse unsafe production configs (section 15)."""
        if self.audit_hmac_key is None or not self.audit_hmac_key.get_secret_value():
            raise ConfigError("AUDIT_HMAC_KEY is required")
        if self.llm_provider == "hosted" and (
            self.hosted_llm_api_key is None or not self.hosted_llm_model
        ):
            raise ConfigError("LLM_PROVIDER=hosted needs HOSTED_LLM_API_KEY and HOSTED_LLM_MODEL")
        if self.environment != "production":
            return
        if self.llm_provider == "fake":
            raise ConfigError("LLM_PROVIDER=fake is not allowed in production")
        if not self.jwks_url:
            raise ConfigError("JWKS_URL is required in production (no static test keys)")
        if self.jwt_public_key_path:
            raise ConfigError("JWT_PUBLIC_KEY_PATH must not be set in production")
        for value in self.secret_values():
            if "CHANGE_ME" in value:
                raise ConfigError("A secret still has its CHANGE_ME placeholder value")


def _password_from_url(url: str | None) -> str | None:
    if not url or "://" not in url:
        return None
    rest = url.split("://", 1)[1]
    if "@" not in rest:
        return None
    userinfo = rest.rsplit("@", 1)[0]
    if ":" not in userinfo:
        return None
    return userinfo.split(":", 1)[1] or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
