"""Textual-based live monitor for enrich progress.

Read-only TUI that reads ``data/claw.db`` + ``data/enrich_logs/*.jsonl`` +
``data/enrich.lock`` and scans ``/proc`` for active ``claw enrich``
subprocesses. Designed for parallel lanes (Plan B): the lanes panel detects
every running enricher via /proc, not just the single lock file.

Launch any time, ``q`` / Ctrl-C to exit — the running enrich/crawl processes
are not touched.
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from sqlmodel import select
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Static

from ..config import get_settings
from ..models.db import Advisor, School, init_db, session_scope


# DeepSeek v4-flash unit pricing (USD per 1M tokens). Tune in code if model changes.
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
    """Walk /proc and return one entry per *python* ``claw enrich`` process."""
    out: list[dict] = []
    proc = Path("/proc")
    if not proc.exists():
        return out
    now = time.time()
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
        if ".venv/bin/python" not in cmdline:
            continue
        school = _school_from_cmd(cmdline)
        try:
            etime = now - (p / "cmdline").stat().st_mtime
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
    # stable ordering by school code, for consistent lane numbering
    out.sort(key=lambda x: x["school"])
    return out


def _summarize_jsonl(log_dir: Path) -> tuple[dict[str, dict], dict, int]:
    """Walk enrich_logs/*.jsonl, group rows by school code."""
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
# Visual helpers
# ----------------------------------------------------------------------------


def _bar(pct: float, width: int = 14) -> str:
    """Unicode block progress bar."""
    pct = max(0, min(100, pct))
    full = int(width * pct / 100)
    frac = (width * pct / 100) - full
    eighths = " ▏▎▍▌▋▊▉█"
    partial = eighths[int(frac * 8)] if full < width else ""
    return "█" * full + partial + " " * max(0, width - full - (1 if partial else 0))


def _bar_color(pct: float) -> str:
    if pct >= 100:
        return "green"
    if pct >= 67:
        return "cyan"
    if pct >= 33:
        return "yellow"
    if pct > 0:
        return "magenta"
    return "grey50"


def _fmt_etime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h{m:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def _fmt_eta(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


# ----------------------------------------------------------------------------
# Textual App
# ----------------------------------------------------------------------------


class EnrichMonitor(App):
    """Flicker-free live enrich monitor — Plan B 2-lane aware."""

    CSS = """
    Screen {
        background: $surface;
    }

    #title_bar {
        height: 1;
        background: $surface-lighten-1;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }

    /* lanes row — sized by its children (round-cornered cards) */
    #lanes_row {
        height: auto;
        padding: 0 1;
        margin: 1 0 0 0;
    }

    .lane_card {
        width: 1fr;
        height: 5;
        border: round $accent;
        padding: 0 1;
        margin: 0 1 0 0;
        background: $surface;
    }

    .lane_card.idle {
        border: round $warning-darken-2;
        color: $text-muted;
    }

    .lane_card.dead {
        border: round $error-darken-2;
        color: $error;
    }

    /* schools table — fills the remaining vertical space (1fr) */
    #schools_table {
        height: 1fr;
        min-height: 8;
        margin: 1 1 0 1;
        border: round $secondary;
    }

    DataTable > .datatable--header {
        background: $surface-lighten-2;
        color: $text;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: $accent 35%;
    }

    DataTable > .datatable--hover {
        background: $boost;
    }

    /* stats row — also sized by children; cards are tall enough now */
    #stats_row {
        height: auto;
        padding: 0 1;
        margin: 1 0 0 0;
    }

    .stat_card {
        width: 1fr;
        height: 7;
        border: round $secondary;
        padding: 0 1;
        margin: 0 1 0 0;
        background: $surface;
    }

    .stat_card.advisors {
        border: round $accent;
    }
    .stat_card.tokens {
        border: round $warning;
    }
    .stat_card.throughput {
        border: round $success;
    }

    /* Footer — drop the default deep blue, blend with surface and only
       highlight the key chips */
    Footer {
        background: $surface;
        color: $text-muted;
    }
    Footer > .footer-key--key {
        background: $accent-darken-2;
        color: $text;
        text-style: bold;
    }
    Footer > .footer-key--description {
        background: $surface;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    def __init__(self, refresh_s: float = 3.0) -> None:
        super().__init__()
        self.refresh_s = max(1.0, refresh_s)
        self.db_path = Path(get_settings().claw_db_path)
        self.log_dir = Path("data/enrich_logs")
        self.lock_path = Path("data/enrich.lock")
        # sliding window of (monotonic_ts, total_jsonl_rows) for throughput
        self._tput_samples: deque[tuple[float, int]] = deque(maxlen=20)

    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="title_bar")
        with Horizontal(id="lanes_row"):
            yield Static("", classes="lane_card", id="lane_card_0")
            yield Static("", classes="lane_card", id="lane_card_1")
        yield DataTable(id="schools_table", zebra_stripes=True, show_cursor=False)
        with Horizontal(id="stats_row"):
            yield Static("", classes="stat_card advisors", id="card_advisors")
            yield Static("", classes="stat_card tokens", id="card_tokens")
            yield Static("", classes="stat_card throughput", id="card_throughput")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "supervisor-claw"
        self.sub_title = "enrich monitor"
        table = self.query_one("#schools_table", DataTable)
        for col, w in [
            ("school", 10),
            ("progress", 22),
            ("enriched", 12),
            ("recruit", 8),
            ("email", 8),
            ("rows", 6),
            ("ok/err", 8),
            ("tokens", 14),
            ("state", 18),
        ]:
            table.add_column(col, width=w)
        self.refresh_display()
        self.set_interval(self.refresh_s, self.refresh_display)

    def action_refresh_now(self) -> None:
        self.refresh_display()

    # ------------------------------------------------------------------

    def _compute_throughput(self, total_rows: int) -> tuple[float, int]:
        """Return (advisors_per_min, eta_seconds_remaining).

        Uses sliding window of last N samples; ignores samples that haven't
        progressed (rows didn't grow).
        """
        now = time.monotonic()
        self._tput_samples.append((now, total_rows))
        if len(self._tput_samples) < 2:
            return 0.0, 0
        t0, r0 = self._tput_samples[0]
        t1, r1 = self._tput_samples[-1]
        dt = t1 - t0
        dr = r1 - r0
        if dt < 1 or dr <= 0:
            return 0.0, 0
        per_min = dr * 60 / dt
        return per_min, 0  # eta filled by caller

    def refresh_display(self) -> None:
        now_utc = datetime.now(timezone.utc)
        active = _scan_active_enrichers()
        active_schools = {a["school"] for a in active}
        lock = _read_lock(self.lock_path)
        per_jsonl, totals, n_files = _summarize_jsonl(self.log_dir)
        db_rows = _db_summary()

        # ---- title bar ----
        self.query_one("#title_bar", Static).update(
            Text.from_markup(
                f"⚡ supervisor-claw · enrich monitor   "
                f"[dim]{now_utc.isoformat(timespec='seconds')}Z · "
                f"refresh {self.refresh_s:.0f}s · active lanes: "
                f"{len(active)}[/]"
            )
        )

        # ---- lane cards (up to 2 visible, more would need responsive layout) ----
        for slot in range(2):
            card = self.query_one(f"#lane_card_{slot}", Static)
            if slot < len(active):
                a = active[slot]
                alive = _pid_alive(a["pid"])
                lane_letter = chr(ord("A") + slot)
                if alive:
                    card.set_class(True, "lane_card")
                    card.set_class(False, "idle")
                    card.set_class(False, "dead")
                else:
                    card.set_class(True, "dead")
                badge = "[bold green]●[/]" if alive else "[bold red]✗[/]"
                school = a["school"]
                pid = a["pid"]
                # Look up that school's current progress
                j = per_jsonl.get(school, {})
                jrows = j.get("rows", 0)
                tot = next((r["total"] for r in db_rows if r["code"] == school), 0)
                pct = (jrows * 100 / tot) if tot else 0
                bar = _bar(pct, width=20)
                color = _bar_color(pct)
                card.update(Text.from_markup(
                    f"[bold]Lane {lane_letter}[/]  {badge} [bold magenta]"
                    f"{school}[/]\n"
                    f"[{color}]{bar}[/]  [bold]{pct:5.1f}%[/]  "
                    f"[dim]{jrows}/{tot}[/]\n"
                    f"[dim]pid {pid} · uptime {_fmt_etime(a['etime_s'])}[/]"
                ))
            else:
                card.set_class(False, "dead")
                card.set_class(True, "idle")
                card.update(Text.from_markup(
                    f"[bold]Lane {chr(ord('A') + slot)}[/]  "
                    f"[dim]· idle ·[/]\n\n[dim]no active enricher on this slot[/]"
                ))

        # ---- per-school table ----
        table = self.query_one("#schools_table", DataTable)
        table.clear()
        for r in sorted(db_rows, key=lambda x: -x["total"]):
            code = r["code"]
            n = r["total"]
            done = r["enriched"]
            pct = (done * 100 / n) if n else 0
            j = per_jsonl.get(code, {})
            jrows = j.get("rows", 0)
            ok = j.get("ok", 0)
            err = j.get("err", 0)
            prompt = j.get("prompt", 0)
            completion = j.get("completion", 0)

            bar_color = _bar_color(pct)
            bar_str = _bar(pct, width=14)
            progress_cell = Text.from_markup(
                f"[{bar_color}]{bar_str}[/] [bold]{pct:4.0f}%[/]"
            )

            if code in active_schools:
                state = Text.from_markup("[bold green]● enriching[/]")
            elif n == 0:
                state = Text.from_markup("[dim]— empty[/]")
            elif done == 0:
                state = Text.from_markup("[yellow]⏸ queued[/]")
            elif done >= n:
                state = Text.from_markup("[bold green]✓ done[/]")
            else:
                state = Text.from_markup(f"[cyan]◌ {pct:.0f}%[/]")

            enriched_cell = Text.from_markup(
                f"[bold]{done}[/][dim]/{n}[/]"
            )
            tok_str = (
                f"{prompt // 1000}k[dim]/[/]{completion // 1000}k"
                if (prompt or completion) else "[dim]—[/]"
            )
            ok_err_cell = (
                Text.from_markup(f"[green]{ok}[/][dim]/[/][red]{err}[/]")
                if (ok or err) else Text.from_markup("[dim]—[/]")
            )
            recruit_str = (
                Text.from_markup(f"[magenta]{r['recruit']}[/]")
                if r["recruit"] else Text("0", style="dim")
            )
            email_str = (
                Text.from_markup(f"[cyan]{r['with_email']}[/]")
                if r["with_email"] else Text("0", style="dim")
            )
            jrows_cell = (
                Text.from_markup(f"[blue]{jrows}[/]") if jrows
                else Text("—", style="dim")
            )

            table.add_row(
                Text(code, style="bold"),
                progress_cell,
                enriched_cell,
                recruit_str,
                email_str,
                jrows_cell,
                ok_err_cell,
                Text.from_markup(tok_str),
                state,
            )

        # ---- aggregate cards ----
        agg_total = sum(r["total"] for r in db_rows)
        agg_enriched = sum(r["enriched"] for r in db_rows)
        agg_recruit = sum(r["recruit"] for r in db_rows)
        agg_email = sum(r["with_email"] for r in db_rows)
        agg_pct = agg_enriched * 100 / max(1, agg_total)
        agg_bar = _bar(agg_pct, width=22)
        agg_color = _bar_color(agg_pct)
        cost = (
            totals["prompt"] / 1_000_000 * PRICE_PROMPT_PER_M
            + totals["completion"] / 1_000_000 * PRICE_COMPLETION_PER_M
        )

        per_min, _ = self._compute_throughput(totals["rows"])
        remaining = max(0, agg_total - agg_enriched)
        eta_s = int(remaining / per_min * 60) if per_min > 0 else 0

        self.query_one("#card_advisors", Static).update(Text.from_markup(
            f"[bold dim]ADVISORS[/]\n"
            f"[bold bright_cyan]{agg_enriched:,}[/]"
            f"[dim] / {agg_total:,}[/]   [bold]{agg_pct:.1f}%[/]\n"
            f"[{agg_color}]{agg_bar}[/]\n"
            f"[dim]recruit {agg_recruit} · email {agg_email}[/]"
        ))

        self.query_one("#card_tokens", Static).update(Text.from_markup(
            f"[bold dim]TOKENS · COST[/]\n"
            f"[bold yellow]{_fmt_tokens(totals['prompt'])}[/] in   "
            f"[bold yellow]{_fmt_tokens(totals['completion'])}[/] out\n"
            f"[bold green]≈ ${cost:.2f}[/]\n"
            f"[dim]v4-flash · ok {totals['ok']} / err {totals['err']}[/]"
        ))

        if per_min > 0:
            tput_line = f"[bold green]{per_min:.1f}[/] advisor/min"
            eta_line = f"[bold]ETA[/] [cyan]{_fmt_eta(eta_s)}[/]"
        else:
            tput_line = "[dim]warming up…[/]"
            eta_line = "[dim]ETA —[/]"
        self.query_one("#card_throughput", Static).update(Text.from_markup(
            f"[bold dim]THROUGHPUT · ETA[/]\n"
            f"{tput_line}\n"
            f"{eta_line}\n"
            f"[dim]{remaining:,} advisor remaining[/]"
        ))


def run_watch_tui(refresh_s: float = 3.0) -> None:
    """Entry point used by ``claw watch``."""
    init_db()
    EnrichMonitor(refresh_s=refresh_s).run()
