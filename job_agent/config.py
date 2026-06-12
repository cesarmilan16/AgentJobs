from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Secrets:
    openai_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    proxy_list: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    raw: dict[str, Any]
    secrets: Secrets
    config_path: Path

    @property
    def threshold(self) -> int:
        return int(self.raw.get("threshold", 60))

    @property
    def db_path(self) -> Path:
        return Path(self.raw["storage"]["db_path"])

    @property
    def log_path(self) -> Path:
        return Path(self.raw["storage"]["log_path"])


def _require(name: str, *, allow_empty: bool = False) -> str:
    val = os.getenv(name, "").strip()
    if not val and not allow_empty:
        raise RuntimeError(
            f"Missing required env var {name}. Copy .env.example to .env and fill it in."
        )
    return val


def load_config(
    config_path: str | Path = "config.yaml",
    *,
    require_secrets: bool = True,
) -> AppConfig:
    load_dotenv(override=False)

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if require_secrets:
        secrets = Secrets(
            openai_api_key=_require("OPENAI_API_KEY"),
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            proxy_list=[p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()],
        )
    else:
        secrets = Secrets(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            proxy_list=[p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()],
        )

    return AppConfig(raw=raw, secrets=secrets, config_path=path)
