"""Typed configuration for the initial ReproScout foundation."""

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Minimal application settings used by the Phase 0 project."""

    runs_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("REPROSCOUT_RUNS_DIR", "runs"))
    )


def get_settings() -> Settings:
    """Return settings from the current environment."""

    return Settings()
