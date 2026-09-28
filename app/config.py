from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDERS = ("anthropic", "openai", "gemini", "ollama")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://feedler:feedler@localhost:5432/feedler"
    secret_key: str = ""
    base_url: str = "http://localhost:8000"

    default_llm_provider: str = "anthropic"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_profile_model: str = "claude-sonnet-5"

    openai_api_key: str = ""
    openai_model: str = ""
    openai_profile_model: str = ""

    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_profile_model: str = ""

    ollama_base_url: str = "http://ollama:11434/v1"
    ollama_model: str = "qwen2.5:7b"
    ollama_profile_model: str = ""

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "feedler/0.1 (self-hosted feed filter)"

    default_poll_interval_minutes: int = 15
    item_max_age_days: int = 3
    profile_refine_after_votes: int = 10

    @property
    def reddit_oauth(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    def provider_configured(self, provider: str) -> bool:
        return {
            "anthropic": bool(self.anthropic_api_key),
            "openai": bool(self.openai_api_key and self.openai_model),
            "gemini": bool(self.gemini_api_key and self.gemini_model),
            "ollama": bool(self.ollama_base_url and self.ollama_model),
        }.get(provider, False)

    def default_model(self, provider: str, purpose: str = "classify") -> str:
        classify = getattr(self, f"{provider}_model", "")
        if purpose == "profile":
            return getattr(self, f"{provider}_profile_model", "") or classify
        return classify

    def batch_size(self, provider: str) -> int:
        # Small local models have small context windows
        return 5 if provider == "ollama" else 20

    def item_char_budget(self, provider: str) -> int:
        return 1500 if provider == "ollama" else 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()
