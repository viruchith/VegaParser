"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Runtime settings for the orders service."""

    service_name: str
    debug: bool


def load_settings() -> Settings:
    """Read settings from environment variables."""
    return Settings(
        service_name=os.environ.get("SERVICE_NAME", "orders"),
        debug=os.environ.get("DEBUG", "0") == "1",
    )
