from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = Field(default="sqlite:///data/market_info_layer.db", alias="DATABASE_URL")
    sec_user_agent: str = Field(
        default="MarketInfoLayer contact@example.com", alias="SEC_USER_AGENT"
    )
    fred_api_key: str | None = Field(default=None, alias="FRED_API_KEY")

    def resolved_database_url(self) -> str:
        if self.database_url.startswith("sqlite:///") and not self.database_url.startswith(
            "sqlite:////"
        ):
            rel = self.database_url.removeprefix("sqlite:///")
            return f"sqlite:///{ROOT_DIR / rel}"
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")
    return Settings()
