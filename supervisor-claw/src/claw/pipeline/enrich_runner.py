"""Batch agent-enrichment pipeline (v0.3).

Drives `enrichers.web_research.research_advisor` across many advisors with:
- last_enriched_at filter for incremental re-runs
- bounded concurrency (Semaphore + per-task SQLModel session)
- per-advisor token-usage telemetry written to data/enrich_logs/<ts>.jsonl
- aggregate EnrichStats returned to the caller
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from ..config import find_school
from ..core.browser import browser_pool
from ..core.logging import get_logger
from ..enrichers.web_research import AgentResult, research_advisor
from ..models.db import Advisor, Appointment, Department, School, init_db, session_scope

log = get_logger(__name__)


@dataclass
class EnrichStats:
    candidates: int = 0           # rows that matched the filter
    processed: int = 0            # advisors the agent actually ran on
    report_submitted: int = 0     # successful submit_report
    no_report: int = 0            # agent finished without submit_report
    errors: int = 0               # exceptions during agent run
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_seconds: float = 0.0
    log_path: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class _Target:
    advisor_id: int
    name_cn: str
    school_name: str
    dept_name: str


def _select_targets(
    school_code: str,
    dept_codes: list[str] | None,
    only_missing: bool,
    stale_days: int,
    limit: int | None,
) -> list[_Target]:
    """Build the list of advisors to enrich. Done in a short-lived session."""
    cutoff = datetime.utcnow() - timedelta(days=stale_days)
    targets: list[_Target] = []
    with session_scope() as s:
        school = s.exec(select(School).where(School.code == school_code)).first()
        if school is None:
            return []
        rows = s.exec(select(Advisor).where(Advisor.school_id == school.id)).all()
        for a in rows:
            if only_missing and a.last_enriched_at is not None and a.last_enriched_at > cutoff:
                continue
            appt = s.exec(
                select(Appointment).where(Appointment.advisor_id == a.id).limit(1)
            ).first()
            dept_name = "(unknown dept)"
            dept_code = None
            if appt:
                d = s.exec(select(Department).where(Department.id == appt.department_id)).first()
                if d:
                    dept_name = d.name_cn
                    dept_code = d.code
            if dept_codes and dept_code not in dept_codes:
                continue
            targets.append(_Target(a.id, a.name_cn, school.name_cn, dept_name))
            if limit and len(targets) >= limit:
                break
    return targets


def _log_path() -> Path:
    out = Path("data/enrich_logs")
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return out / f"{ts}.jsonl"


def _append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _short_summary(res: AgentResult | None, err: str | None) -> str:
    if err:
        return f"ERR {err[:60]}"
    if res is None:
        return "no result"
    parts = []
    if res.report_submitted:
        ir = res.is_recruiting
        ir_s = "招生" if ir is True else ("不招" if ir is False else "未知")
        parts.append(f"{ir_s}·{res.reputation_tag}·conf={res.recruiting_confidence:.1f}")
    else:
        parts.append("no_report")
    parts.append(f"ev+{res.evaluations_written}")
    parts.append(f"q+{res.quotas_written}")
    parts.append(f"tok={res.total_tokens}")
    return " ".join(parts)


async def enrich_with_agent(
    *,
    school_code: str,
    dept_codes: list[str] | None = None,
    only_missing: bool = True,
    stale_days: int = 30,
    limit: int | None = None,
    max_iter: int = 8,
    concurrency: int = 2,
    headed: bool = False,
    on_advisor_done=None,  # callable(idx, total, target, res, err) — for CLI progress
) -> EnrichStats:
    """Run the research agent on a batch of advisors.

    Concurrency: shared BrowserPool + shared bing context with multiple pages
    open in parallel. DeepSeek calls are I/O-bound; the practical ceiling is
    the DeepSeek per-account rate limit (~3 RPS), so concurrency 2-3 is the
    sweet spot.
    """
    init_db()  # ensures v0.3 migration columns are present
    if find_school(school_code) is None:
        raise ValueError(f"School '{school_code}' not in schools.yaml")

    targets = _select_targets(school_code, dept_codes, only_missing, stale_days, limit)
    stats = EnrichStats(candidates=len(targets))
    log_path = _log_path()
    stats.log_path = str(log_path)

    if not targets:
        log.info(
            "enrich: 0 candidates for school=%s only_missing=%s",
            school_code, only_missing,
        )
        return stats

    log.info(
        "enrich start: school=%s candidates=%d concurrency=%d max_iter=%d log=%s",
        school_code, len(targets), concurrency, max_iter, log_path,
    )

    t0 = time.time()
    sem = asyncio.Semaphore(max(1, concurrency))
    done_n = 0
    done_lock = asyncio.Lock()

    async with browser_pool(headless=not headed) as pool:

        async def _one(idx: int, t: _Target) -> None:
            nonlocal done_n
            async with sem:
                res: AgentResult | None = None
                err: str | None = None
                with session_scope() as s:
                    advisor = s.get(Advisor, t.advisor_id)
                    if advisor is None:
                        err = "advisor row vanished mid-batch"
                    else:
                        try:
                            res = await research_advisor(
                                advisor, t.school_name, t.dept_name, pool, s,
                                max_iter=max_iter,
                            )
                        except Exception as e:  # noqa: BLE001
                            err = f"{type(e).__name__}: {e}"
                            log.exception("agent crashed for %s", t.name_cn)

                # ---- aggregate stats (single-thread asyncio: safe to += here)
                stats.processed += 1
                if err:
                    stats.errors += 1
                if res is not None:
                    if res.report_submitted:
                        stats.report_submitted += 1
                    else:
                        stats.no_report += 1
                    stats.prompt_tokens += res.prompt_tokens
                    stats.completion_tokens += res.completion_tokens

                _append_jsonl(log_path, {
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "school": school_code,
                    "advisor_id": t.advisor_id,
                    "name_cn": t.name_cn,
                    "dept": t.dept_name,
                    "ok": err is None,
                    "error": err,
                    **(asdict(res) if res is not None else {}),
                })

                async with done_lock:
                    done_n += 1
                    n = done_n
                if on_advisor_done is not None:
                    try:
                        on_advisor_done(n, len(targets), t, res, err)
                    except Exception:  # noqa: BLE001
                        log.exception("on_advisor_done callback failed")

        await asyncio.gather(*[_one(i, t) for i, t in enumerate(targets)])

    stats.wall_seconds = time.time() - t0
    log.info(
        "enrich done: processed=%d report=%d no_report=%d errors=%d "
        "tokens={in:%d, out:%d} wall=%.1fs",
        stats.processed, stats.report_submitted, stats.no_report,
        stats.errors, stats.prompt_tokens, stats.completion_tokens, stats.wall_seconds,
    )
    return stats
