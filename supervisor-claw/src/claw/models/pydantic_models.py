"""Pydantic DTOs used by adapters before they hit the DB."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdvisorPartial(BaseModel):
    name_cn: str
    name_en: str | None = None
    title: str | None = None
    gender: str | None = None
    homepage: str | None = None
    email: str | None = None
    email_obfuscated: bool = False
    phone: str | None = None
    photo_url: str | None = None
    bio_text: str | None = None
    research_interests: list[str] = Field(default_factory=list)
    raw_quota_text: str | None = None
    is_recruiting: bool | None = None
    source_url: str | None = None  # the page this advisor was scraped from

    def research_interests_csv(self) -> str | None:
        if not self.research_interests:
            return None
        return ", ".join(t.strip() for t in self.research_interests if t.strip())
