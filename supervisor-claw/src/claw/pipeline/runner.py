"""Pipeline: discover → fetch → parse → store → snapshot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..adapters import get_adapter
from ..config import SchoolConfig, find_school, get_settings
from ..core.http import Fetcher, ForbiddenByRobots
from ..core.logging import get_logger
from ..core.snapshot import write_snapshot
from ..models.db import init_db
from ..storage.repo import (
    link_department,
    record_snapshot,
    session_scope,
    upsert_advisor,
    upsert_department,
    upsert_school,
)

log = get_logger(__name__)


@dataclass
class CrawlStats:
    list_pages: int = 0
    profiles_fetched: int = 0
    advisors_upserted: int = 0
    errors: int = 0
    skipped_robots: int = 0


async def crawl_school(
    school_code: str,
    *,
    dept_codes: list[str] | None = None,
    limit: int | None = None,
    snapshot: bool = True,
) -> CrawlStats:
    init_db()
    school_cfg = find_school(school_code)
    if school_cfg is None:
        raise ValueError(f"School '{school_code}' not in schools.yaml")
    adapter = get_adapter(school_code)
    stats = CrawlStats()

    async with Fetcher() as fetcher:
        with session_scope() as s:
            school_row = upsert_school(s, school_cfg.code, school_cfg.name_cn, school_cfg.name_en)

            for dept in school_cfg.departments:
                if dept_codes and dept.code not in dept_codes:
                    continue
                if not adapter.supports_dept(dept.code):
                    log.info(
                        "%s: adapter does not support dept '%s' yet; skipping",
                        school_code, dept.code,
                    )
                    continue

                primary_list_url = dept.list_urls[0] if dept.list_urls else None
                dept_row = upsert_department(s, school_row, dept.code, dept.name_cn, primary_list_url)

                for list_url in dept.list_urls:
                    await _process_list(
                        fetcher=fetcher,
                        adapter=adapter,
                        school=school_row,
                        dept=dept_row,
                        list_url=list_url,
                        session=s,
                        stats=stats,
                        limit=limit,
                        snapshot=snapshot,
                    )
                    if limit and stats.advisors_upserted >= limit:
                        return stats

    return stats


async def _process_list(
    *,
    fetcher,
    adapter,
    school,
    dept,
    list_url: str,
    session,
    stats: CrawlStats,
    limit: int | None,
    snapshot: bool,
) -> None:
    log.info("[%s/%s] list: %s", school.code, dept.code, list_url)
    try:
        r = await fetcher.get(list_url)
    except ForbiddenByRobots:
        stats.skipped_robots += 1
        return
    except Exception as e:
        log.error("list fetch failed: %s — %s", list_url, e)
        stats.errors += 1
        return
    stats.list_pages += 1

    if snapshot:
        sha, path = write_snapshot(f"{school.code}-{dept.code}", list_url, r.content)
        record_snapshot(session, list_url, sha, str(path))

    try:
        items = adapter.parse_list(r.text, list_url)
    except Exception as e:
        log.exception("parse_list failed for %s: %s", list_url, e)
        stats.errors += 1
        return
    log.info("[%s/%s] parsed %d list items", school.code, dept.code, len(items))

    for item in items:
        if limit and stats.advisors_upserted >= limit:
            return
        await _process_profile(
            fetcher=fetcher,
            adapter=adapter,
            school=school,
            dept=dept,
            item=item,
            session=session,
            stats=stats,
            snapshot=snapshot,
        )


async def _process_profile(
    *,
    fetcher,
    adapter,
    school,
    dept,
    item,
    session,
    stats: CrawlStats,
    snapshot: bool,
) -> None:
    partial = None
    if item.profile_url:
        try:
            r = await fetcher.get(item.profile_url)
            stats.profiles_fetched += 1
            if snapshot:
                sha, path = write_snapshot(
                    f"{school.code}-{dept.code}", item.profile_url, r.content
                )
                record_snapshot(session, item.profile_url, sha, str(path))
            partial = adapter.parse_profile(r.text, item.profile_url, item)
        except ForbiddenByRobots:
            stats.skipped_robots += 1
        except Exception as e:
            log.warning("profile fetch/parse failed for %s (%s): %s", item.name_cn, item.profile_url, e)
            stats.errors += 1

    if partial is None:
        # fall back to list-only data (still upsert basic info)
        from ..models.pydantic_models import AdvisorPartial
        partial = AdvisorPartial(
            name_cn=item.name_cn,
            title=item.title,
            email=item.email,
            phone=item.phone,
            photo_url=item.photo_url,
            homepage=item.profile_url,
            source_url=item.profile_url,
        )

    advisor = upsert_advisor(session, school, partial)
    link_department(session, advisor, dept)
    stats.advisors_upserted += 1


def crawl_school_sync(school_code: str, **kwargs) -> CrawlStats:
    return asyncio.run(crawl_school(school_code, **kwargs))
