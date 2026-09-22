"""Central configuration. Everything is read from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional for tests
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    # Paths
    root_dir: Path = ROOT_DIR
    knowledge_dir: Path = ROOT_DIR / "knowledge"
    cache_dir: Path = ROOT_DIR / "data" / "cache"
    mock_dir: Path = ROOT_DIR / "data" / "mock"
    output_dir: Path = ROOT_DIR / "outputs"

    # ZoomInfo (OAuth 2.0 client credentials, GTM API)
    zoominfo_client_id: str = field(default_factory=lambda: _env("ZOOMINFO_CLIENT_ID"))
    zoominfo_client_secret: str = field(default_factory=lambda: _env("ZOOMINFO_CLIENT_SECRET"))
    zoominfo_base_url: str = field(default_factory=lambda: _env("ZOOMINFO_BASE_URL", "https://api.zoominfo.com/gtm"))
    zoominfo_token_url: str = field(default_factory=lambda: _env("ZOOMINFO_TOKEN_URL", "https://api.zoominfo.com/gtm/oauth/v1/token"))
    zoominfo_scope: str = field(default_factory=lambda: _env("ZOOMINFO_SCOPE"))
    default_country: str = field(default_factory=lambda: _env("DEFAULT_COUNTRY", "United Kingdom"))

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "gemini").lower())
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5"))

    # Sender details used in email sign-off
    sender_name: str = field(default_factory=lambda: _env("SENDER_NAME", "Your Name"))
    sender_title: str = field(default_factory=lambda: _env("SENDER_TITLE", "Employer Partnerships"))
    sender_email: str = field(default_factory=lambda: _env("SENDER_EMAIL", "support@wtd.org.uk"))
    sender_phone: str = field(default_factory=lambda: _env("SENDER_PHONE", "020 3411 0180"))

    # Workspace-wide Microsoft email limit.
    daily_send_limit: int = field(default_factory=lambda: int(_env("DAILY_SEND_LIMIT", "30")))

    # Web API. Secrets are never returned by the settings endpoint.
    api_key: str = field(default_factory=lambda: _env("API_KEY"))
    database_path: Path = field(default_factory=lambda: Path(_env("DATABASE_PATH") or str(ROOT_DIR / "data" / "app.sqlite3")))
    cors_origins: list[str] = field(default_factory=lambda: [v.strip() for v in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if v.strip()])

    # Optional Microsoft mailbox connection. Never required to open the workspace.
    microsoft_tenant_id: str = field(default_factory=lambda: _env("MICROSOFT_TENANT_ID").lower())
    microsoft_client_id: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_ID"))
    microsoft_client_secret: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_SECRET"))
    microsoft_redirect_uri: str = field(default_factory=lambda: _env("MICROSOFT_REDIRECT_URI", "http://localhost:3000/api/auth/microsoft/callback"))
    # Optional shared mailbox. Exchange must grant the connecting user access.
    microsoft_mailbox_address: str = field(default_factory=lambda: _env("MICROSOFT_MAILBOX_ADDRESS").lower())
    # Use its Entra object ID / UPN here if the mail address is only an alias.
    microsoft_mailbox_user_id: str = field(default_factory=lambda: _env("MICROSOFT_MAILBOX_USER_ID"))
    token_encryption_key: str = field(default_factory=lambda: _env("TOKEN_ENCRYPTION_KEY"))

    @property
    def has_microsoft(self) -> bool:
        return all((self.microsoft_tenant_id, self.microsoft_client_id, self.microsoft_client_secret,
                    self.microsoft_redirect_uri, self.token_encryption_key))

    @property
    def has_zoominfo(self) -> bool:
        return bool(self.zoominfo_client_id and self.zoominfo_client_secret)


settings = Settings()