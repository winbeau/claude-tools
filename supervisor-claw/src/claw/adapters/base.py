"""Adapter abstract base + registry."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import ClassVar

from ..models.pydantic_models import AdvisorPartial


@dataclass
class ListItem:
    """Lightweight result from parsing a faculty list page."""
    name_cn: str
    profile_url: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    photo_url: str | None = None


class SchoolAdapter(abc.ABC):
    school_code: ClassVar[str]
    supports: ClassVar[set[str]]  # department codes this adapter handles

    @abc.abstractmethod
    def parse_list(self, html: str, list_url: str) -> list[ListItem]:
        ...

    @abc.abstractmethod
    def parse_profile(
        self, html: str, profile_url: str, list_item: ListItem
    ) -> AdvisorPartial:
        ...

    def supports_dept(self, dept_code: str) -> bool:
        return dept_code in self.supports


REGISTRY: dict[str, type[SchoolAdapter]] = {}


def register(cls: type[SchoolAdapter]) -> type[SchoolAdapter]:
    REGISTRY[cls.school_code] = cls
    return cls
