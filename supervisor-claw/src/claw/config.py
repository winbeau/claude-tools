from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DepartmentConfig(BaseModel):
    code: str
    name_cn: str
    name_en: str | None = None
    list_urls: list[str] = Field(default_factory=list)


class SchoolConfig(BaseModel):
    code: str
    name_cn: str
    name_en: str | None = None
    departments: list[DepartmentConfig] = Field(default_factory=list)


class SchoolsFile(BaseModel):
    schools: list[SchoolConfig]


class Settings(BaseSettings):
    """Process-wide settings loaded from .env + environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    claw_contact_email: str = "anonymous@example.com"
    claw_db_path: Path = Path("data/claw.db")
    claw_rps: float = 0.5
    claw_snapshot_dir: Path = Path("data/snapshots")
    claw_session_dir: Path = Path("data/sessions")
    claw_schools_yaml: Path = Path("schools.yaml")

    @property
    def user_agent(self) -> str:
        return f"supervisor-claw/0.1 (+contact: {self.claw_contact_email})"


@cache
def get_settings() -> Settings:
    return Settings()


@cache
def load_schools(path: Path | None = None) -> SchoolsFile:
    p = path or get_settings().claw_schools_yaml
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return SchoolsFile.model_validate(data)


def find_school(code: str) -> SchoolConfig | None:
    for s in load_schools().schools:
        if s.code == code:
            return s
    return None
