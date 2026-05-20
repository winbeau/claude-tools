"""Batch agent-enrichment pipeline (v0.3).

Drives `enrichers.web_research.research_advisor` across many advisors with:
- last_enriched_at filter for incremental re-runs (resume-safe by default)
- bounded concurrency (Semaphore + per-task SQLModel session)
- per-advisor token-usage telemetry written to data/enrich_logs/<ts>.jsonl
- aggregate EnrichStats returned to the caller
- pre-flight banner + PID-based lock file (v0.3.1) to make accidental
  re-runs visible and prevent two enrich processes from clobbering each other
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
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


# ---- v0.3.1 lock file ----------------------------------------------------

LOCK_PATH = Path("data/enrich.lock")


class EnrichLocked(RuntimeError):
    """Another enrich is already running."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we can't signal it
    return True


@contextlib.asynccontextmanager
async def _acquire_lock(force: bool = False):
    """File-based mutex on data/enrich.lock (async-context for clean nesting).

    - if lock exists and the writer PID is alive → raise EnrichLocked
    - if lock exists but the writer PID is dead → stale, overwrite with warning
    - on clean exit (or signal) → remove the lock
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            payload = json.loads(LOCK_PATH.read_text())
            other_pid = int(payload.get("pid", 0))
        except Exception:
            other_pid = 0
        if other_pid and _pid_alive(other_pid) and not force:
            raise EnrichLocked(
                f"another enrich is running (pid={other_pid}, "
                f"started={payload.get('start')}). Use --force to override."
            )
        log.warning(
            "stale enrich lock at %s (pid=%s no longer alive); overwriting",
            LOCK_PATH, other_pid,
        )

    me = {
        "pid": os.getpid(),
        "start": datetime.utcnow().isoformat() + "Z",
        "cmd": " ".join(sys.argv),
    }
    LOCK_PATH.write_text(json.dumps(me, ensure_ascii=False))
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            LOCK_PATH.unlink()


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


def _count_states(school_code: str, stale_days: int) -> tuple[int, int, int]:
    """(total, fresh_enriched, pending) — for the pre-flight banner.

    'fresh_enriched' = last_enriched_at within stale_days; these would be
    skipped under --only-missing. 'pending' = total - fresh_enriched.
    """
    cutoff = datetime.utcnow() - timedelta(days=stale_days)
    with session_scope() as s:
        school = s.exec(select(School).where(School.code == school_code)).first()
        if school is None:
            return (0, 0, 0)
        advs = s.exec(select(Advisor).where(Advisor.school_id == school.id)).all()
        total = len(advs)
        fresh = sum(
            1 for a in advs
            if a.last_enriched_at is not None and a.last_enriched_at > cutoff
        )
        return total, fresh, total - fresh


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
    force_lock: bool = False,
    on_advisor_done=None,  # callable(idx, total, target, res, err) — for CLI progress
    on_preflight=None,     # callable(total, fresh_enriched, pending, will_run) — for CLI banner
) -> EnrichStats:
    """Run the research agent on a batch of advisors.

    Resume semantics (v0.3.1):
    - only_missing=True (default) → skip advisors enriched within stale_days;
      this is the safe "resume from where we stopped" mode
    - only_missing=False (--force-redo / --all) → redo everyone, will be loud
      about already-fresh rows in the pre-flight banner

    Process safety (v0.3.1):
    - A PID-keyed lock file at data/enrich.lock prevents two concurrent
      enrich processes from clobbering each other's DB writes.

    Concurrency: shared BrowserPool + shared bing context with multiple pages
    open in parallel. DeepSeek calls are I/O-bound; the practical ceiling is
    the DeepSeek per-account rate limit (~3 RPS), so concurrency 2-3 is the
    sweet spot.
    """
    init_db()  # ensures v0.3 migration columns are present
    if find_school(school_code) is None:
        raise ValueError(f"School '{school_code}' not in schools.yaml")

    # ---- pre-flight banner ----------------------------------------------
    total, fresh, pending = _count_states(school_code, stale_days)
    will_run = pending if only_missing else total
    if on_preflight is not None:
        on_preflight(total, fresh, pending, will_run)
    else:
        log.info(
            "preflight: school=%s total=%d already_enriched_<=%dd=%d pending=%d "
            "mode=%s will_run=%d",
            school_code, total, stale_days, fresh, pending,
            "only-missing" if only_missing else "force-redo (--all)",
            will_run,
        )
    if not only_missing and fresh > 0:
        log.warning(
            "⚠️  --force-redo / --all will REDO %d already-enriched advisor(s). "
            "If you meant 'resume from where we stopped', drop --all.",
            fresh,
        )

    targets = _select_targets(school_code, dept_codes, only_missing, stale_days, limit)
    stats = EnrichStats(candidates=len(targets))
    log_path = _log_path()
    stats.log_path = str(log_path)

    if not targets:
        log.info("enrich: nothing to do (0 candidates) for school=%s", school_code)
        return stats

    log.info(
        "enrich start: school=%s candidates=%d concurrency=%d max_iter=%d log=%s",
        school_code, len(targets), concurrency, max_iter, log_path,
    )

    t0 = time.time()
    sem = asyncio.Semaphore(max(1, concurrency))
    done_n = 0
    done_lock = asyncio.Lock()

    async with _acquire_lock(force=force_lock), browser_pool(headless=not headed) as pool:

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
