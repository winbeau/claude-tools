"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
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
    fmt: str = typer.Option("csv", "--format", help="csv | jsonl"),
    school_code: str = typer.Option(None, "--school", help="filter by school code"),
    recruiting_only: bool = typer.Option(False, "--recruiting-only"),
    out: Path = typer.Option(Path("export.csv"), "--out"),
) -> None:
    """Export advisors to CSV or JSONL."""
    setup_logging()
    init_db()
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


if __name__ == "__main__":
    app()
