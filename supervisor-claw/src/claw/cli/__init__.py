"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from ..config import get_settings, load_schools
from ..core.logging import setup_logging
from ..models.db import Advisor, Appointment, Department, School, init_db, session_scope
from ..pipeline.runner import crawl_school_sync

app = typer.Typer(help="supervisor-claw — local advisor crawler", no_args_is_help=True)
console = Console()


@app.command()
def doctor() -> None:
    """Self-check: DB init, schools.yaml parse, .env presence."""
    setup_logging()
    s = get_settings()
    console.print(f"[bold]DB path[/bold]      : {s.claw_db_path}")
    console.print(f"[bold]Snapshot dir[/bold] : {s.claw_snapshot_dir}")
    console.print(f"[bold]Session dir[/bold]  : {s.claw_session_dir}")
    console.print(f"[bold]UA[/bold]           : {s.user_agent}")
    console.print(f"[bold]Rate (rps)[/bold]   : {s.claw_rps}")
    console.print(
        f"[bold]DeepSeek[/bold]     : key={'set' if s.deepseek_api_key else 'MISSING'} "
        f"base={s.deepseek_base_url} model={s.deepseek_model}"
    )

    init_db()
    console.print("[green]✓[/green] DB initialised")

    schools = load_schools()
    console.print(f"[green]✓[/green] schools.yaml: {len(schools.schools)} schools")
    for sc in schools.schools:
        depts = ", ".join(d.code for d in sc.departments)
        console.print(f"   - {sc.code}: {depts}")

    if not s.deepseek_api_key:
        console.print("[yellow]![/yellow] DEEPSEEK_API_KEY missing — quota_extractor disabled.")

    consent = (Path(".env").exists() or s.deepseek_api_key) and True
    if not consent:
        console.print(
            "\n[yellow]Notice:[/yellow] Personal academic use only. "
            "Do not redistribute / publicly host scraped data. See README for full terms."
        )


@app.command()
def crawl(
    school: str = typer.Argument(..., help="school code, e.g. tsinghua / all"),
    dept: list[str] = typer.Option(None, "--dept", help="department code(s) to limit to"),
    limit: int = typer.Option(0, "--limit", help="stop after N advisors (0 = all)"),
    snapshot: bool = typer.Option(True, "--snapshot/--no-snapshot"),
) -> None:
    """Crawl official school sites."""
    setup_logging()
    targets: list[str]
    if school == "all":
        targets = [s.code for s in load_schools().schools]
    else:
        targets = [school]

    for code in targets:
        console.rule(f"[bold blue]crawl {code}")
        try:
            stats = crawl_school_sync(
                code,
                dept_codes=dept or None,
                limit=limit or None,
                snapshot=snapshot,
            )
        except KeyError as e:
            console.print(f"[red]No adapter for[/red] {code}: {e}")
            continue
        console.print(
            f"[green]{code}[/green] done: "
            f"list_pages={stats.list_pages} profiles={stats.profiles_fetched} "
            f"advisors={stats.advisors_upserted} errors={stats.errors} "
            f"skipped_robots={stats.skipped_robots}"
        )


@app.command()
def export(
    fmt: str = typer.Option("csv", "--format", help="csv | jsonl | sqlite"),
    school_code: str = typer.Option(None, "--school", help="filter by school code (csv/jsonl only)"),
    recruiting_only: bool = typer.Option(False, "--recruiting-only", help="csv/jsonl only"),
    out: Path = typer.Option(None, "--out", help="csv/jsonl: file path (default export.csv); sqlite: dir (default ./exports)"),
) -> None:
    """Export advisors to CSV / JSONL / SQLite snapshot.

    sqlite mode produces ``<out>/schools.sqlite`` + ``<out>/manifest.json``
    for downstream attach (e.g. Aurash schools page).
    """
    setup_logging()
    init_db()

    if fmt == "sqlite":
        from ..export.sqlite_exporter import export_sqlite
        out_dir = out or Path("./exports")
        if school_code or recruiting_only:
            console.print(
                "[yellow]![/yellow] sqlite mode ignores --school/--recruiting-only (full snapshot)"
            )
        summary = export_sqlite(out_dir)
        c = summary.counts
        console.print(
            f"[green]✓[/green] schools.sqlite: "
            f"{c.schools} schools / {c.departments} depts / {c.advisors} advisors / "
            f"{c.quotas} quotas / {c.evaluations} evals / {c.traces} trace rows"
        )
        console.print(
            f"  {summary.db_path} ({summary.bytes:,} bytes, sha={summary.sha256[:12]}…)"
        )
        console.print(f"  manifest → {summary.manifest_path}")
        return

    out = out or Path("export.csv")
    with session_scope() as s:
        q = select(Advisor, School).join(School, Advisor.school_id == School.id)
        if school_code:
            q = q.where(School.code == school_code)
        if recruiting_only:
            q = q.where(Advisor.is_recruiting == True)  # noqa: E712
        rows = s.exec(q).all()

        # also load appointments → dept names
        dept_index: dict[int, list[str]] = {}
        for appt, dept_row in s.exec(
            select(Appointment, Department).join(Department, Appointment.department_id == Department.id)
        ).all():
            dept_index.setdefault(appt.advisor_id, []).append(dept_row.name_cn)

    if fmt == "csv":
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "school", "name_cn", "name_en", "title", "departments",
                "email", "email_obfuscated", "phone", "homepage",
                "research_interests", "is_recruiting", "raw_quota_text",
                "source_url", "last_updated",
            ])
            for advisor, school in rows:
                w.writerow([
                    school.code, advisor.name_cn, advisor.name_en or "",
                    advisor.title or "",
                    "; ".join(dept_index.get(advisor.id, [])),
                    advisor.email or "", advisor.email_obfuscated,
                    advisor.phone or "", advisor.homepage or "",
                    advisor.research_interests_raw or "",
                    "" if advisor.is_recruiting is None else int(advisor.is_recruiting),
                    advisor.raw_quota_text or "",
                    advisor.source_url or "",
                    advisor.last_updated.isoformat() if advisor.last_updated else "",
                ])
    elif fmt == "jsonl":
        with out.open("w", encoding="utf-8") as f:
            for advisor, school in rows:
                payload = {
                    "school": school.code,
                    "departments": dept_index.get(advisor.id, []),
                    **advisor.model_dump(exclude={"id", "school_id"}),
                }
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    else:
        console.print(f"[red]unknown format[/red]: {fmt}")
        sys.exit(2)

    console.print(f"[green]✓[/green] wrote {len(rows)} advisors → {out}")


@app.command()
def stats() -> None:
    """Coverage stats per school."""
    setup_logging()
    init_db()
    with session_scope() as s:
        table = Table(title="advisor coverage")
        table.add_column("school")
        table.add_column("advisors", justify="right")
        table.add_column("with_email", justify="right")
        table.add_column("recruiting", justify="right")
        rows = s.exec(select(School)).all()
        for school in rows:
            advs = s.exec(select(Advisor).where(Advisor.school_id == school.id)).all()
            with_email = sum(1 for a in advs if a.email)
            recruiting = sum(1 for a in advs if a.is_recruiting)
            table.add_row(school.code, str(len(advs)), str(with_email), str(recruiting))
        console.print(table)


@app.command()
def research(
    school_code: str = typer.Option(..., "--school", help="school code, required"),
    name: list[str] = typer.Option(None, "--name", help="target specific advisor(s) by name (repeatable)"),
    limit: int = typer.Option(0, "--limit", help="stop after N advisors (0 = all matched)"),
    max_iter: int = typer.Option(6, "--max-iter", help="max LLM turns per advisor"),
    headed: bool = typer.Option(False, "--headed", help="show Playwright browser (debug)"),
    only_missing: bool = typer.Option(
        True,
        "--only-missing/--all",
        help="skip advisors that already have any evaluation row (default: skip)",
    ),
) -> None:
    """Autonomous research via DeepSeek tool-calling + Playwright bing search.

    For each advisor in --school, runs an agent loop that decides queries,
    reads pages, and writes findings to evaluation / quota_info tables.
    """
    setup_logging()
    init_db()

    # imports kept local — playwright is optional at install time for v0.1 paths
    from ..core.browser import browser_pool
    from ..enrichers.web_research import research_advisor
    from ..models.db import Evaluation  # noqa: F401 — used in the inner closure

    targets: list[tuple[Advisor, str, str]] = []
    with session_scope() as s:
        school = s.exec(select(School).where(School.code == school_code)).first()
        if school is None:
            console.print(f"[red]school '{school_code}' not in DB — run `claw crawl` first[/red]")
            raise typer.Exit(2)
        q = select(Advisor).where(Advisor.school_id == school.id)
        if name:
            q = q.where(Advisor.name_cn.in_(name))  # type: ignore[attr-defined]
        adv_rows = s.exec(q).all()
        # also need dept name (pick first appointment) for the prompt
        for a in adv_rows:
            if only_missing and not name:
                has_eval = s.exec(
                    select(Evaluation).where(Evaluation.advisor_id == a.id).limit(1)
                ).first()
                if has_eval:
                    continue
            appt = s.exec(
                select(Appointment).where(Appointment.advisor_id == a.id).limit(1)
            ).first()
            dept_name = "(unknown dept)"
            if appt:
                d = s.exec(select(Department).where(Department.id == appt.department_id)).first()
                if d:
                    dept_name = d.name_cn
            targets.append((a, school.name_cn, dept_name))
            if limit and len(targets) >= limit:
                break

    if not targets:
        console.print("[yellow]nothing to research (all advisors already have evaluations?)[/yellow]")
        return

    from ..enrichers.research_display import ResearchDisplay, silence_loggers
    import time as _time

    # External loggers (httpx INFO, openai INFO, playwright DEBUG) print above
    # the Live region and cause visible ghosting as the tree re-paints. Mute
    # them for the duration of research.
    silence_loggers()

    display = ResearchDisplay(console)
    display.run_header(len(targets), school_code)

    async def _run() -> tuple[int, int]:
        ok_n = 0
        failed_n = 0
        async with browser_pool(headless=not headed) as pool:
            with session_scope() as s:
                for advisor, school_name, dept_name in targets:
                    a = s.get(Advisor, advisor.id)
                    if a is None:
                        continue
                    with display.advisor(a, school_name, dept_name) as view:
                        try:
                            res = await research_advisor(
                                a, school_name, dept_name, pool, s,
                                max_iter=max_iter, view=view,
                            )
                            view.summary(res)
                            ok_n += 1
                        except Exception as e:  # noqa: BLE001
                            console.print(f"[red]agent crashed for {a.name_cn}: {e}[/red]")
                            failed_n += 1
        return ok_n, failed_n

    t0 = _time.time()
    ok, failed = asyncio.run(_run())
    display.run_footer(ok, failed, _time.time() - t0)


@app.command()
def enrich(
    source: str = typer.Option(
        "agent", "--source",
        help="enrichment source: 'agent' = DeepSeek tool-calling + bing search",
    ),
    school_code: str = typer.Option(..., "--school", help="school code, required"),
    dept: list[str] = typer.Option(None, "--dept", help="dept code(s) to limit to"),
    limit: int = typer.Option(0, "--limit", help="stop after N advisors (0 = all matched)"),
    max_iter: int = typer.Option(8, "--max-iter", help="max LLM turns per advisor"),
    concurrency: int = typer.Option(2, "--concurrency", help="parallel advisor agents (1-4)"),
    only_missing: bool = typer.Option(
        True,
        "--only-missing/--force-redo",
        help="default: skip advisors enriched within --stale-days (resume-safe). "
             "--force-redo: redo everyone, including already-enriched.",
    ),
    all_alias: bool = typer.Option(False, "--all", help="alias for --force-redo (legacy)"),
    stale_days: int = typer.Option(30, "--stale-days", help="how old last_enriched_at must be to re-run"),
    headed: bool = typer.Option(False, "--headed", help="show Playwright browser (debug)"),
    force_lock: bool = typer.Option(False, "--force-unlock", help="override data/enrich.lock (use only after confirming no other enrich is running)"),
) -> None:
    """Batch enrichment: run agent across all (or filtered) advisors of a school."""
    setup_logging()
    init_db()
    if source != "agent":
        console.print(f"[red]unsupported --source[/red] {source!r}; only 'agent' is implemented in v0.3")
        raise typer.Exit(2)

    from ..pipeline.enrich_runner import EnrichLocked, enrich_with_agent

    # --all is a legacy alias for --force-redo (which is now expressed as
    # `--only-missing/--force-redo` → only_missing=False)
    if all_alias:
        only_missing = False

    def _preflight(total: int, fresh: int, pending: int, will_run: int) -> None:
        mode = "[green]only-missing (resume-safe)[/]" if only_missing else "[red]force-redo (will REDO already-enriched)[/]"
        console.print()
        console.print(f"  [bold]{school_code}[/]: {total} advisors total")
        console.print(f"    already enriched (<={stale_days}d): [cyan]{fresh}[/]")
        console.print(f"    pending: [yellow]{pending}[/]")
        console.print(f"    mode: {mode} → will run [bold]{will_run}[/]")
        if not only_missing and fresh > 0:
            console.print(f"  [red]⚠️  --force-redo will REDO {fresh} fresh advisor(s). Ctrl-C in 3s to abort.[/]")

    def _progress(n: int, total: int, t, res, err) -> None:
        from ..pipeline.enrich_runner import _short_summary
        tag = "[red]✗[/red]" if err else (
            "[green]✓[/green]" if (res and res.report_submitted) else "[yellow]·[/yellow]"
        )
        console.print(f"{tag} [{n:>3}/{total}] [bold]{t.name_cn}[/] · {t.dept_name} — {_short_summary(res, err)}")

    console.rule(f"[bold blue]enrich {school_code} (source=agent)")
    try:
        stats = asyncio.run(enrich_with_agent(
            school_code=school_code,
            dept_codes=dept or None,
            only_missing=only_missing,
            stale_days=stale_days,
            limit=limit or None,
            max_iter=max_iter,
            concurrency=concurrency,
            headed=headed,
            force_lock=force_lock,
            on_preflight=_preflight,
            on_advisor_done=_progress,
        ))
    except EnrichLocked as e:
        console.print(f"[red]✗ enrich locked:[/] {e}")
        raise typer.Exit(3)
    console.rule(f"[bold green]done {school_code}")
    console.print(
        f"candidates={stats.candidates} processed={stats.processed} "
        f"[green]report={stats.report_submitted}[/] [yellow]no_report={stats.no_report}[/] "
        f"[red]errors={stats.errors}[/]"
    )
    console.print(
        f"tokens in={stats.prompt_tokens} out={stats.completion_tokens} "
        f"(≈¥{stats.prompt_tokens / 1e6 * 2 + stats.completion_tokens / 1e6 * 8:.3f}) "
        f"wall={stats.wall_seconds:.1f}s"
    )
    console.print(f"log: {stats.log_path}")


@app.command("crawl-stealth")
def crawl_stealth(
    school: str = typer.Argument(..., help="school code, e.g. xjtu"),
    dept: list[str] = typer.Option(None, "--dept", help="dept code(s) to limit to (default all)"),
    limit: int = typer.Option(0, "--limit", help="stop after N advisors (0 = all)"),
    headed: bool = typer.Option(False, "--headed", help="show chromium (debug)"),
    snapshot: bool = typer.Option(True, "--snapshot/--no-snapshot"),
) -> None:
    """Crawl with playwright-stealth + homepage warmup + wayback/dblp fallback.

    For schools blocked by JS-WAF (xjtu / uestc.scse / uestc.sise / xidian
    email-decrypt etc) where the plain httpx Fetcher only sees the WAF stub.
    Three-tier cascade:
      1. stealth chromium navigates to the URL (after warming up the host
         home page so the WAF cookie is set);
      2. if still a stub → Wayback Machine snapshot of the same URL;
      3. if individual profile still missing → dblp author lookup so the
         advisor row at least gets created with bibliographic context.
    """
    setup_logging()
    from ..pipeline.stealth_crawler import crawl_school_with_stealth_sync

    console.rule(f"[bold blue]stealth crawl {school}")
    stats = crawl_school_with_stealth_sync(
        school,
        dept_codes=dept if dept else None,
        headed=headed,
        snapshot=snapshot,
        limit=limit if limit > 0 else None,
    )
    console.print(
        f"[bold]{school}[/] advisors_upserted={stats.advisors_upserted} "
        f"list_ok={stats.list_pages_ok} list_stub={stats.list_pages_stub} "
        f"profile_ok={stats.profiles_ok} profile_stub={stats.profiles_stub} "
        f"wayback={stats.wayback_fallbacks} dblp={stats.dblp_fallbacks} "
        f"errors={stats.errors}"
    )


@app.command("backfill-email")
def backfill_email(
    school: str = typer.Argument(..., help="school code, e.g. xidian / hust / xjtu"),
    limit: int = typer.Option(0, "--limit", help="stop after N advisors (0 = all matched)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="don't write DB, just print candidates"),
    strategy: list[str] = typer.Option(
        None,
        "--strategy",
        help="cascade subset of: js, bing, dblp (default = all 3 in order)",
    ),
    headed: bool = typer.Option(False, "--headed", help="show Playwright browser (debug)"),
    concurrency: int = typer.Option(1, "--concurrency", help="parallel advisors (1 recommended)"),
) -> None:
    """Backfill ``advisor.email`` for the given school.

    Idempotent: only operates on advisors where ``email IS NULL`` and writes
    via :func:`update_email_only` which refuses to overwrite existing values.
    Every write is mirrored to ``data/email_backfill_audit.jsonl``.

    Strategies cascade per advisor — the first hit wins.
    """
    setup_logging()
    init_db()

    # imports kept local — playwright is optional during unit-test imports
    import httpx

    from ..config import find_school
    from ..core.http import make_legacy_friendly_ssl_context
    from ..enrichers.email_backfill import backfill_one_advisor, update_email_only
    from ..pipeline.stealth_crawler import open_stealth_session

    cfg = find_school(school)
    if cfg is None:
        console.print(f"[red]school '{school}' not in schools.yaml[/red]")
        raise typer.Exit(2)

    strategies = list(strategy) if strategy else ["js", "bing", "dblp"]
    valid = {"js", "bing", "dblp"}
    for s in strategies:
        if s not in valid:
            console.print(f"[red]unknown strategy '{s}'; valid: {sorted(valid)}[/red]")
            raise typer.Exit(2)

    # ---- select candidates (advisors with email IS NULL) ----
    candidates: list[tuple[int, str, str | None]] = []
    with session_scope() as s:
        school_row = s.exec(select(School).where(School.code == school)).first()
        if school_row is None:
            console.print(f"[red]school '{school}' has no rows in DB — run `claw crawl` first[/red]")
            raise typer.Exit(2)
        q = select(Advisor).where(
            Advisor.school_id == school_row.id, Advisor.email.is_(None)  # type: ignore[union-attr]
        )
        rows = s.exec(q).all()
        for a in rows:
            candidates.append((a.id, a.name_cn, a.homepage))
            if limit and len(candidates) >= limit:
                break

    total = len(candidates)
    if total == 0:
        console.print(f"[green]{school}[/green]: no advisors with NULL email — nothing to do")
        return

    console.rule(f"[bold blue]backfill-email {school} ({total} candidates)")
    console.print(
        f"strategies={strategies}  dry_run={dry_run}  audit=data/email_backfill_audit.jsonl"
    )

    hits = 0

    async def _run() -> int:
        nonlocal hits
        ssl_ctx = make_legacy_friendly_ssl_context()
        async with open_stealth_session(headed=headed) as sess_stealth:
            page = await sess_stealth.context.new_page()
            async with httpx.AsyncClient(timeout=20.0, verify=ssl_ctx, http2=True) as client:
                with session_scope() as db:
                    for i, (advisor_id, name_cn, _homepage) in enumerate(candidates, 1):
                        advisor = db.get(Advisor, advisor_id)
                        if advisor is None:
                            continue
                        if advisor.email:  # raced — skip
                            continue
                        try:
                            email, source = await backfill_one_advisor(
                                advisor=advisor,
                                page=page,
                                sess=client,
                                school_code=school,
                                school_name_cn=cfg.name_cn,
                                strategies=strategies,
                            )
                        except Exception as e:  # noqa: BLE001
                            console.print(f"[red]✗[/red] [{i}/{total}] {name_cn}: {e}")
                            continue

                        if email:
                            if dry_run:
                                console.print(
                                    f"[cyan]·[/cyan] [{i}/{total}] {name_cn} "
                                    f"→ [bold]{email}[/] (source={source}, dry-run)"
                                )
                                hits += 1
                            else:
                                wrote = update_email_only(
                                    db, advisor_id, email, source or "unknown"
                                )
                                if wrote:
                                    hits += 1
                                    console.print(
                                        f"[green]✓[/green] [{i}/{total}] {name_cn} "
                                        f"→ [bold]{email}[/] (source={source})"
                                    )
                                else:
                                    console.print(
                                        f"[yellow]·[/yellow] [{i}/{total}] {name_cn} "
                                        f"→ {email} (already set / write rejected)"
                                    )
                        else:
                            console.print(
                                f"[dim]–[/dim] [{i}/{total}] {name_cn} — no email found"
                            )

                        # politeness: short sleep between advisors to avoid ban.
                        await asyncio.sleep(1.5)
            with contextlib.suppress(Exception):
                await page.close()
        return hits

    hits = asyncio.run(_run())
    pct = (hits * 100 / total) if total else 0
    console.rule(f"[bold green]done {school}")
    console.print(
        f"{hits}/{total} advisors got email ({pct:.1f}%); "
        f"audit at data/email_backfill_audit.jsonl"
    )


@app.command()
def watch(
    refresh_s: float = typer.Option(1.0, "--refresh", help="seconds between TUI refreshes"),
) -> None:
    """Live read-only TUI for enrich progress (Textual-based, multi-lane aware).

    Reads DB + data/enrich_logs/*.jsonl + data/enrich.lock and scans /proc for
    active `claw enrich` processes (detects parallel lanes). Refreshes every
    --refresh seconds (default 1s). q / Ctrl-C to quit — running processes
    are not touched.
    """
    from .watch_tui import run_watch_tui

    run_watch_tui(refresh_s=refresh_s)


def _watch_legacy(refresh_s: float) -> None:
    """Legacy Rich.Live implementation, kept as fallback only."""
    import time as _t
    from datetime import datetime, timezone

    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.text import Text

    init_db()
    s_settings = get_settings()
    db_path = Path(s_settings.claw_db_path)
    enrich_lock_path = Path("data/enrich.lock")
    enrich_log_dir = Path("data/enrich_logs")
    # v4-flash unit pricing (per 1M tokens, USD).  Override via env later if needed.
    PRICE_PROMPT_PER_M = 0.27
    PRICE_COMPLETION_PER_M = 1.10

    def _read_lock() -> dict | None:
        if not enrich_lock_path.exists():
            return None
        try:
            d = json.loads(enrich_lock_path.read_text())
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _school_from_cmd(cmd: str) -> str | None:
        # Parse "...claw enrich --school <code> --concurrency 2 [--force-unlock]"
        toks = cmd.split()
        for i, t in enumerate(toks):
            if t == "--school" and i + 1 < len(toks):
                return toks[i + 1]
        return None

    def _pid_alive(pid: int) -> bool:
        try:
            import os
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _summarize_jsonl_files() -> tuple[dict[str, dict], dict, int]:
        """Walk enrich_logs/*.jsonl, group rows by school.

        Returns (per_school, totals, total_files).
        per_school[school_code] = {
            rows, ok, err, report, no_report, prompt_tok, completion_tok,
            last_advisor, last_ts, first_ts
        }
        """
        per: dict[str, dict] = {}
        totals = {
            "rows": 0, "ok": 0, "err": 0, "report": 0, "no_report": 0,
            "prompt": 0, "completion": 0,
        }
        if not enrich_log_dir.exists():
            return per, totals, 0
        files = sorted(enrich_log_dir.glob("*.jsonl"))
        for f in files:
            try:
                with f.open() as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        sc = d.get("school", "?")
                        rec = per.setdefault(
                            sc,
                            {"rows": 0, "ok": 0, "err": 0, "report": 0, "no_report": 0,
                             "prompt": 0, "completion": 0, "last_advisor": "",
                             "last_ts": "", "first_ts": ""},
                        )
                        rec["rows"] += 1
                        totals["rows"] += 1
                        ok = bool(d.get("ok"))
                        if ok:
                            rec["ok"] += 1; totals["ok"] += 1
                            if d.get("report_submitted"):
                                rec["report"] += 1; totals["report"] += 1
                            else:
                                rec["no_report"] += 1; totals["no_report"] += 1
                        else:
                            rec["err"] += 1; totals["err"] += 1
                        rec["prompt"] += int(d.get("prompt_tokens", 0) or 0)
                        rec["completion"] += int(d.get("completion_tokens", 0) or 0)
                        totals["prompt"] += int(d.get("prompt_tokens", 0) or 0)
                        totals["completion"] += int(d.get("completion_tokens", 0) or 0)
                        ts = d.get("ts", "")
                        if ts:
                            if not rec["first_ts"]:
                                rec["first_ts"] = ts
                            rec["last_ts"] = ts
                        rec["last_advisor"] = d.get("name_cn", rec["last_advisor"])
            except Exception:
                continue
        return per, totals, len(files)

    def _build() -> Layout:
        now_utc = datetime.now(timezone.utc)
        lock = _read_lock()
        per_jsonl, totals, n_files = _summarize_jsonl_files()

        # ---- DB per-school summary ----
        with session_scope() as sess:
            schools = sess.exec(select(School)).all()
            db_rows: list[dict] = []
            agg_total = 0
            agg_enriched = 0
            agg_recruit = 0
            agg_with_email = 0
            for sch in schools:
                advs = sess.exec(select(Advisor).where(Advisor.school_id == sch.id)).all()
                n = len(advs)
                enriched = sum(1 for a in advs if a.last_enriched_at is not None)
                recruit = sum(1 for a in advs if a.is_recruiting)
                with_email = sum(1 for a in advs if a.email)
                db_rows.append({
                    "code": sch.code,
                    "total": n,
                    "enriched": enriched,
                    "recruit": recruit,
                    "with_email": with_email,
                })
                agg_total += n
                agg_enriched += enriched
                agg_recruit += recruit
                agg_with_email += with_email

        # ---- per-school table ----
        tbl = Table(
            title=None, expand=True, header_style="bold cyan",
            row_styles=["", "dim"],
        )
        tbl.add_column("school", style="bold", no_wrap=True)
        tbl.add_column("crawled", justify="right")
        tbl.add_column("enriched", justify="right")
        tbl.add_column("done%", justify="right")
        tbl.add_column("recruit", justify="right")
        tbl.add_column("email", justify="right")
        tbl.add_column("jsonl rows", justify="right")
        tbl.add_column("ok/err", justify="right")
        tbl.add_column("tok in/out", justify="right")
        tbl.add_column("state")
        active_school = _school_from_cmd(lock.get("cmd", "")) if lock else None
        for r in sorted(db_rows, key=lambda x: -x["total"]):
            code = r["code"]
            n = r["total"]
            done = r["enriched"]
            pct = (done * 100 / n) if n else 0
            j = per_jsonl.get(code, {})
            jsonl_rows = j.get("rows", 0)
            ok = j.get("ok", 0)
            err = j.get("err", 0)
            prompt = j.get("prompt", 0)
            completion = j.get("completion", 0)
            state = ""
            if active_school == code:
                state = "[bold green]● enriching[/]"
            elif n == 0:
                state = "[dim]– empty –[/]"
            elif done == 0:
                state = "[yellow]⌛ queued[/]"
            elif done >= n:
                state = "[green]✓ done[/]"
            else:
                state = f"[cyan]◌ partial {pct:.0f}%[/]"
            tok_str = f"{prompt//1000}k/{completion//1000}k" if (prompt or completion) else "-"
            tbl.add_row(
                code, str(n), str(done), f"{pct:.0f}%",
                str(r["recruit"]), str(r["with_email"]),
                str(jsonl_rows), f"{ok}/{err}", tok_str, state,
            )

        # ---- header ----
        head_lines: list[str] = []
        head_lines.append(
            f"[bold]supervisor-claw / enrich monitor[/]   "
            f"[dim]now {now_utc.isoformat(timespec='seconds')}Z   "
            f"refresh={refresh_s:.0f}s   db={db_path}[/]"
        )
        if lock:
            pid = int(lock.get("pid", 0))
            alive = _pid_alive(pid) if pid else False
            sch = active_school or "?"
            started = lock.get("start", "")
            tag = "[bold green]● live[/]" if alive else "[red]✗ dead-pid[/]"
            head_lines.append(
                f"{tag}  enrich pid={pid} school=[bold magenta]{sch}[/] "
                f"started={started}"
            )
        else:
            head_lines.append("[yellow]○ no active enrich (no lock file)[/]")
        header = Panel(Text.from_markup("\n".join(head_lines)), border_style="blue")

        # ---- footer ----
        cost = (
            totals["prompt"] / 1_000_000 * PRICE_PROMPT_PER_M
            + totals["completion"] / 1_000_000 * PRICE_COMPLETION_PER_M
        )
        foot = (
            f"[bold]aggregate[/]  "
            f"advisors {agg_enriched}/{agg_total} enriched ({agg_enriched*100/max(1,agg_total):.0f}%) · "
            f"recruit {agg_recruit} · email {agg_with_email}\n"
            f"[bold]enrich runs[/]  "
            f"jsonl files {n_files} · rows {totals['rows']} · "
            f"ok {totals['ok']} / err {totals['err']} / report {totals['report']} / "
            f"no_report {totals['no_report']}\n"
            f"[bold]tokens[/]  "
            f"prompt {totals['prompt']:,} · completion {totals['completion']:,} · "
            f"[green]cost ≈ ${cost:.2f}[/]   [dim](v4-flash @ "
            f"${PRICE_PROMPT_PER_M}/M in + ${PRICE_COMPLETION_PER_M}/M out)[/]"
        )
        footer = Panel(Text.from_markup(foot), border_style="green")

        lay = Layout()
        lay.split_column(
            Layout(header, name="head", size=4),
            Layout(tbl, name="body"),
            Layout(footer, name="foot", size=6),
        )
        return lay

    with Live(_build(), refresh_per_second=max(0.2, 1.0 / refresh_s), screen=False) as live:
        try:
            while True:
                _t.sleep(refresh_s)
                live.update(_build())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    app()
