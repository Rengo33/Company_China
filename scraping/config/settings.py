"""Central configuration — loads API keys and settings from .env file."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_path: str = str(Path(__file__).resolve().parent.parent / "leads.db")

    # API keys (all free-tier)
    companies_house_api_key: str = ""  # free at developer.company-information.service.gov.uk
    google_api_key: str = ""           # free at console.cloud.google.com

    # Facebook Ad Library — free, create app at developers.facebook.com
    facebook_app_token: str = ""

    # Proxy (optional — needed for CN site access)
    proxy_url: str = ""

    # Rate limits (seconds between requests)
    rate_limit_default: float = 2.0
    rate_limit_companies_house: float = 0.5  # 600 req / 5 min
    rate_limit_scrape: float = 3.0           # polite delay for website scraping
    rate_limit_amazon: float = 7.0           # Amazon needs slow, randomized delays
    rate_limit_facebook: float = 1.0         # 200 req/hour
    rate_limit_aliexpress: float = 10.0

    # Assessment thresholds
    poor_website_threshold: int = 55  # score below this = high-value lead

    # Export
    export_dir: str = str(Path(__file__).resolve().parent.parent / "exports")


settings = Settings()
