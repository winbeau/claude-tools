"""Textual-based live monitor for enrich progress.

Read-only TUI that reads ``data/claw.db`` + ``data/enrich_logs/*.jsonl`` +
``data/enrich.lock`` and scans ``/proc`` for active ``claw enrich``
subprocesses. Detects **multiple** parallel enrichers (e.g. Lane A + Lane B).
Launch any time, Ctrl-C / q to exit — the running enrich/crawl processes are
not touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from sqlmodel import select
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from ..config import get_settings
from ..models.db import Advisor, School, init_db, session_scope


# DeepSeek v4-flash unit pricing (USD per 1M tokens) — override in code if needed.
PRICE_PROMPT_PER_M = 0.27
PRICE_COMPLETION_PER_M = 1.10


# ----------------------------------------------------------------------------
# Stateless probes
# ----------------------------------------------------------------------------


def _read_lock(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _school_from_cmd(cmd: str) -> str | None:
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


def _scan_active_enrichers() -> list[dict]:
    """Walk /proc and return one entry per *python* ``claw enrich`` process.

    Skips the ``uv run`` wrappers — only the actual Python process running the
    agent loop is returned. Each entry has pid / school / start_etime_seconds.
    """
    out: list[dict] = []
    proc = Path("/proc")
    if not proc.exists():
        return out
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmdline_raw = (p / "cmdline").read_text()
        except Exception:
            continue
        if not cmdline_raw:
            continue
        cmdline = cmdline_raw.replace("\x00", " ").strip()
        if "claw" not in cmdline or "enrich" not in cmdline or "--school" not in cmdline:
            continue
        # Only count the Python process (not the uv wrapper).
        if ".venv/bin/python" not in cmdline:
            continue
        school = _school_from_cmd(cmdline)
        # Read process start time from /proc/<pid>/stat (clock-ticks since boot)
        # then convert to seconds via os.sysconf("SC_CLK_TCK"). For our purpose
        # we just want elapsed seconds — simpler: read /proc/<pid>/status's
        # starttime via fstat on cmdline file (mtime ≈ start time, close enough).
        try:
            etime = (
                datetime.now(timezone.utc).timestamp()
                - (p / "cmdline").stat().st_mtime
            )
        except Exception:
            etime = 0
        out.append(
            {
                "pid": int(p.name),
                "school": school or "?",
                "etime_s": int(etime),
                "cmdline": cmdline[:160],
            }
        )
    return out


def _summarize_jsonl(log_dir: Path) -> tuple[dict[str, dict], dict, int]:
    """Walk enrich_logs/*.jsonl, group rows by school code.

    Returns ``(per_school, totals, n_files)``.
    """
    per: dict[str, dict] = {}
    totals = {
        "rows": 0, "ok": 0, "err": 0, "report": 0, "no_report": 0,
        "prompt": 0, "completion": 0,
    }
    if not log_dir.exists():
        return per, totals, 0
    files = sorted(log_dir.glob("*.jsonl"))
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
                        {
                            "rows": 0, "ok": 0, "err": 0, "report": 0,
                            "no_report": 0, "prompt": 0, "completion": 0,
                            "last_advisor": "", "last_ts": "",
                        },
                    )
                    rec["rows"] += 1
                    totals["rows"] += 1
                    ok = bool(d.get("ok"))
                    if ok:
                        rec["ok"] += 1
                        totals["ok"] += 1
                        if d.get("report_submitted"):
                            rec["report"] += 1
                            totals["report"] += 1
                        else:
                            rec["no_report"] += 1
                            totals["no_report"] += 1
                    else:
                        rec["err"] += 1
                        totals["err"] += 1
                    pt = int(d.get("prompt_tokens", 0) or 0)
                    ct = int(d.get("completion_tokens", 0) or 0)
                    rec["prompt"] += pt
                    rec["completion"] += ct
                    totals["prompt"] += pt
                    totals["completion"] += ct
                    if d.get("ts"):
                        rec["last_ts"] = d["ts"]
                    if d.get("name_cn"):
                        rec["last_advisor"] = d["name_cn"]
        except Exception:
            continue
    return per, totals, len(files)


def _db_summary() -> list[dict]:
    """One row per school: total / enriched / recruit / with_email."""
    out: list[dict] = []
    with session_scope() as sess:
        schools = sess.exec(select(School)).all()
        for sch in schools:
            advs = sess.exec(select(Advisor).where(Advisor.school_id == sch.id)).all()
            n = len(advs)
            enriched = sum(1 for a in advs if a.last_enriched_at is not None)
            recruit = sum(1 for a in advs if a.is_recruiting)
            with_email = sum(1 for a in advs if a.email)
            out.append(
                {
                    "code": sch.code,
                    "total": n,
                    "enriched": enriched,
                    "recruit": recruit,
                    "with_email": with_email,
                }
            )
    return out


# ----------------------------------------------------------------------------
# Textual App
# ----------------------------------------------------------------------------


class EnrichMonitor(App):
    """Textual app — flicker-free live enrich monitor."""

    CSS = """
    Screen {
        layers: base;
    }
    #lane_panel {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: tall $primary;
    }
    #schools_table {
        height: 1fr;
    }
    #aggregate {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: tall $success;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh now"),
    ]

    def __init__(self, refresh_s: float = 3.0) -> None:
        super().__init__()
        self.refresh_s = max(1.0, refresh_s)
        self.db_path = Path(get_settings().claw_db_path)
        self.log_dir = Path("data/enrich_logs")
        self.lock_path = Path("data/enrich.lock")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("loading…", id="lane_panel")
        yield DataTable(id="schools_table", zebra_stripes=True)
        yield Static("loading…", id="aggregate")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "supervisor-claw / enrich monitor"
        table = self.query_one("#schools_table", DataTable)
        table.cursor_type = "row"
        for col, w in [
            ("school", 10), ("crawled", 8), ("enriched", 9), ("done%", 7),
            ("recruit", 8), ("email", 7), ("jsonl", 7), ("ok/err", 9),
            ("tok in/out", 13), ("state", 22),
        ]:
            table.add_column(col, width=w)
        self.refresh_display()
        self.set_interval(self.refresh_s, self.refresh_display)

    def action_refresh_now(self) -> None:
        self.refresh_display()

    # ------------------------------------------------------------------

    def refresh_display(self) -> None:
        now_utc = datetime.now(timezone.utc)
        active = _scan_active_enrichers()
        active_schools = {a["school"] for a in active}
        lock = _read_lock(self.lock_path)
        per_jsonl, totals, n_files = _summarize_jsonl(self.log_dir)
        db_rows = _db_summary()

        # ---- lane panel (top) ----
        lane_lines: list[str] = []
        lane_lines.append(
            f"[dim]now {now_utc.isoformat(timespec='seconds')}Z   "
            f"refresh={self.refresh_s:.0f}s   db={self.db_path}   "
            f"active enrichers: {len(active)}[/]"
        )
        if not active:
            lane_lines.append("[yellow]○ no active enrich process detected in /proc[/]")
        else:
            for i, a in enumerate(active):
                tag = ["[green]●[/]", "[cyan]●[/]"][i % 2]
                etime_min = a["etime_s"] // 60
                etime_sec = a["etime_s"] % 60
                lane_lines.append(
                    f"{tag} lane{i + 1}  pid={a['pid']}  "
                    f"school=[bold magenta]{a['school']}[/]  "
                    f"alive={etime_min}m{etime_sec:02d}s"
                )
        if lock:
            lk_pid = int(lock.get("pid", 0))
            lk_sch = _school_from_cmd(lock.get("cmd", "")) or "?"
            lk_alive = "[green]live[/]" if _pid_alive(lk_pid) else "[red]dead[/]"
            lane_lines.append(
                f"[dim]lock file → pid={lk_pid} school={lk_sch} {lk_alive}[/]"
            )
        else:
            lane_lines.append("[dim]lock file → none[/]")
        self.query_one("#lane_panel", Static).update(Text.from_markup("\n".join(lane_lines)))

        # ---- per-school table ----
        table = self.query_one("#schools_table", DataTable)
        table.clear()
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
            if code in active_schools:
                state = Text.from_markup("[bold green]● enriching[/]")
            elif n == 0:
                state = Text.from_markup("[dim]– empty –[/]")
            elif done == 0:
                state = Text.from_markup("[yellow]⌛ queued[/]")
            elif done >= n:
                state = Text.from_markup("[green]✓ done[/]")
            else:
                state = Text.from_markup(f"[cyan]◌ partial {pct:.0f}%[/]")
            tok_str = (
                f"{prompt // 1000}k/{completion // 1000}k"
                if (prompt or completion) else "-"
            )
            err_style = "red" if err else "white"
            table.add_row(
                code,
                str(n),
                str(done),
                f"{pct:.0f}%",
                str(r["recruit"]),
                str(r["with_email"]),
                str(jsonl_rows),
                Text(f"{ok}/{err}", style=err_style),
                tok_str,
                state,
            )

        # ---- aggregate footer ----
        agg_total = sum(r["total"] for r in db_rows)
        agg_enriched = sum(r["enriched"] for r in db_rows)
        agg_recruit = sum(r["recruit"] for r in db_rows)
        agg_email = sum(r["with_email"] for r in db_rows)
        cost = (
            totals["prompt"] / 1_000_000 * PRICE_PROMPT_PER_M
            + totals["completion"] / 1_000_000 * PRICE_COMPLETION_PER_M
        )
        pct_total = agg_enriched * 100 / max(1, agg_total)
        agg_lines = [
            f"[bold]advisors[/]  {agg_enriched}/{agg_total} enriched "
            f"({pct_total:.0f}%) · recruit {agg_recruit} · email {agg_email}",
            f"[bold]enrich runs[/]  jsonl files {n_files} · rows {totals['rows']} · "
            f"ok {totals['ok']} / err {totals['err']} / "
            f"report {totals['report']} / no_report {totals['no_report']}",
            f"[bold]tokens[/]  prompt {totals['prompt']:,} · "
            f"completion {totals['completion']:,} · "
            f"[green]cost ≈ ${cost:.2f}[/] [dim]"
            f"(@ ${PRICE_PROMPT_PER_M}/M in + ${PRICE_COMPLETION_PER_M}/M out)[/]",
        ]
        self.query_one("#aggregate", Static).update(
            Text.from_markup("\n".join(agg_lines))
        )


def run_watch_tui(refresh_s: float = 3.0) -> None:
    """Entry point used by ``claw watch``."""
    init_db()
    EnrichMonitor(refresh_s=refresh_s).run()
