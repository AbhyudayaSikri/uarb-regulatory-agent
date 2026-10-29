"""Runtime configuration, loaded from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Inbound mail
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"

    # Outbound mail
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_ssl: bool = False

    # Behaviour
    portal_url: str = UARB_URL
    max_documents: int = Field(default=10, ge=1, le=50)
    max_attachment_mb: float = Field(default=20.0, gt=0)
    poll_interval_seconds: int = Field(default=60, ge=5)
    headless: bool = True
    browser_channel: Literal["chromium", "chrome", "msedge"] = "chromium"
    allowed_senders: list[str] = Field(default_factory=list)
    rate_limit_per_sender_per_hour: int = Field(default=6, ge=0)
    work_dir: Path = Path("./out")
    page_timeout_ms: int = Field(default=60_000, ge=5_000)

    # Optional LLM
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"

    @field_validator("allowed_senders", mode="before")
    @classmethod
    def _split_senders(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip().lower() for part in value.split(",") if part.strip()]
        return [str(v).strip().lower() for v in value]  # type: ignore[union-attr]

    @property
    def mail_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.smtp_host and self.smtp_from)

    @property
    def max_attachment_bytes(self) -> int:
        return int(self.max_attachment_mb * 1024 * 1024)

    def sender_allowed(self, address: str) -> bool:
        if not self.allowed_senders:
            return True
        address = address.lower().strip()
        domain = address.rsplit("@", 1)[-1] if "@" in address else ""
        for rule in self.allowed_senders:
            if rule.startswith("@") and domain == rule[1:]:
                return True
            if rule == address:
                return True
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
