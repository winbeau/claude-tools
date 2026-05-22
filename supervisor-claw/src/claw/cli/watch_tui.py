"""Textual-based live monitor for enrich / crawl / email-backfill progress.

Read-only TUI that reads ``data/claw.db`` + ``data/enrich_logs/*.jsonl`` +
``data/email_backfill_*.log`` + ``data/email_backfill_audit.jsonl`` and
scans ``/proc`` for any running ``claw enrich`` / ``claw crawl`` /
``claw crawl-stealth`` / ``claw backfill-email`` subprocess. The lane
panel shows up to 5 active processes regardless of task type — the same
lane is reused if the running task happens to be email backfill instead
of enrichment.

Launch any time, ``q`` / Ctrl-C to exit — the running subprocesses are
not touched.
"""

from __future__ import annotations

import json
import time
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


def _classify_claw_cmd(cmd: str) -> tuple[str, str | None] | None:
    """Classify a ``claw …`` command line. Returns (task, school) or None.

    task ∈ {"enrich", "crawl", "backfill-email"}. ``school`` is the school
    code (from --school or, for backfill-email, the positional arg).
    """
    if "claw" not in cmd:
        return None
    toks = cmd.split()
    # Find the subcommand right after the 'claw' bin / module token.
    sub = None
    for i, t in enumerate(toks):
        if t.endswith("/claw") or t == "claw":
            if i + 1 < len(toks):
                sub = toks[i + 1]
                break
    if sub is None:
        return None
    if sub == "enrich":
        return ("enrich", _school_from_cmd(cmd))
    if sub in ("crawl", "crawl-stealth"):
        return ("crawl", _school_from_cmd(cmd))
    if sub == "backfill-email":
        # Positional: claw backfill-email <school> [--flags…]
        # Find the next non-flag token after the subcommand.
        try:
            after = toks[toks.index(sub) + 1:]
        except ValueError:
            after = []
        school = None
        for t in after:
            if not t.startswith("--"):
                school = t
                break
        return ("backfill-email", school)
    return None


def _pid_alive(pid: int) -> bool:
    try:
        import os

        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _scan_active_processes() -> list[dict]:
    """Walk /proc, return one entry per active ``claw`` subprocess.

    Recognises enrich / crawl / crawl-stealth / backfill-email. Wrapper
    ``uv run …`` processes are filtered out; only the underlying
    ``.venv/bin/python … claw …`` worker is returned (avoids each task
    appearing twice).
    """
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
        if "claw" not in cmdline:
            continue
        if ".venv/bin/python" not in cmdline:
            # skip the outer `uv run` wrapper; the inner python process
            # is the actual worker we want to track.
            continue
        cls = _classify_claw_cmd(cmdline)
        if cls is None:
            continue
        task, school = cls
        try:
            etime = now - (p / "cmdline").stat().st_mtime
        except Exception:
            etime = 0
        out.append(
            {
                "pid": int(p.name),
                "task": task,
                "school": school or "?",
                "etime_s": int(etime),
                "cmdline": cmdline[:160],
            }
        )
    # stable ordering: task type then school, for consistent lane numbering
    out.sort(key=lambda x: (x["task"], x["school"]))
    return out


# Back-compat shim — older code paths may import the old name.
_scan_active_enrichers = _scan_active_processes


def _email_backfill_progress(
    school: str, data_dir: Path = Path("data")
) -> tuple[int, int, int, str | None]:
    """Read the most recent ``email_backfill_<school>_*.log`` for progress.

    Returns ``(done, hits, timeouts, last_advisor)``. ``done`` counts any
    per-advisor outcome line (✓ ✗ ⏱ – ·). All zeros if no log exists.
    """
    if not data_dir.exists():
        return 0, 0, 0, None
    logs = sorted(data_dir.glob(f"email_backfill_{school}_*.log"))
    if not logs:
        return 0, 0, 0, None
    log = logs[-1]
    done = hits = timeouts = 0
    last_name: str | None = None
    try:
        with log.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line:
                    continue
                head = line[:1]
                if head in ("✓", "✗", "⏱", "·", "–", "-"):
                    done += 1
                    if head == "✓":
                        hits += 1
                    elif head == "⏱":
                        timeouts += 1
                    # the line shape is: "<sym> [i/total] <name> — …"
                    try:
                        # find name between "] " and " —"
                        b = line.find("] ")
                        e = line.find(" —", b + 2)
                        if b != -1 and e != -1:
                            last_name = line[b + 2 : e].strip() or last_name
                    except Exception:
                        pass
    except Exception:
        pass
    return done, hits, timeouts, last_name


def _email_backfill_total(school: str, data_dir: Path = Path("data")) -> int:
    """Read backfill-email header line ``backfill-email <school> (N candidates)``.

    Returns 0 if no log / header not found.
    """
    if not data_dir.exists():
        return 0
    logs = sorted(data_dir.glob(f"email_backfill_{school}_*.log"))
    if not logs:
        return 0
    log = logs[-1]
    try:
        with log.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "candidates" in line and "backfill-email" in line:
                    # e.g. "──── backfill-email xidian (229 candidates) ────"
                    i = line.find("(")
                    j = line.find(" candidates", i)
                    if i != -1 and j != -1:
                        try:
                            return int(line[i + 1 : j])
                        except ValueError:
                            return 0
                    return 0
    except Exception:
        pass
    return 0


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

    def __init__(self, refresh_s: float = 1.0) -> None:
        super().__init__()
        self.refresh_s = max(1.0, refresh_s)
        self.db_path = Path(get_settings().claw_db_path)
        self.log_dir = Path("data/enrich_logs")
        self.lock_path = Path("data/enrich.lock")
        # EMA-smoothed throughput. Sample every 10s; α=0.1 → ~2-minute
        # effective window. Once we've ever seen progress, never revert to
        # the "warming up" label (rate may decay slightly when momentarily
        # idle, but won't flip back to 0).
        self._tput_last_sample: tuple[float, int] | None = None
        self._tput_ema: float = 0.0
        self._tput_has_progress: bool = False
        self._TPUT_SAMPLE_INTERVAL_S: float = 10.0
        self._TPUT_EMA_ALPHA: float = 0.10
        self._TPUT_IDLE_DECAY: float = 0.97  # per sample when no new rows

    # ------------------------------------------------------------------

    LANES = 5  # max simultaneous lane cards (enrich + crawl + backfill mix)

    def compose(self) -> ComposeResult:
        yield Static("", id="title_bar")
        with Horizontal(id="lanes_row"):
            for i in range(self.LANES):
                yield Static("", classes="lane_card", id=f"lane_card_{i}")
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
        """Return (advisors_per_min, _).

        EMA-smoothed: take a new sample every `_TPUT_SAMPLE_INTERVAL_S`
        seconds; on each sample blend the instantaneous rate (dr/dt) into
        `_tput_ema` with factor α. Idle samples decay slowly so the value
        never flips back to "warming up" once any progress has been seen.
        """
        now = time.monotonic()

        # first call → anchor and bail
        if self._tput_last_sample is None:
            self._tput_last_sample = (now, total_rows)
            return 0.0, 0

        t0, r0 = self._tput_last_sample
        dt = now - t0

        # haven't reached next sample tick yet → keep showing the smoothed value
        if dt < self._TPUT_SAMPLE_INTERVAL_S:
            return self._tput_ema, 0

        dr = total_rows - r0

        if dr > 0:
            instant = dr * 60 / dt  # advisors/min over this sample
            if self._tput_ema == 0:
                # seed with the first observed instantaneous rate so we
                # don't lag during the initial minute
                self._tput_ema = instant
            else:
                a = self._TPUT_EMA_ALPHA
                self._tput_ema = a * instant + (1 - a) * self._tput_ema
            self._tput_has_progress = True
        elif self._tput_has_progress:
            # No new rows this tick → decay slowly toward 0 but stay > 0
            # so the "warming up" branch is never re-entered.
            self._tput_ema *= self._TPUT_IDLE_DECAY
        # else: first-time warm-up, leave _tput_ema at 0

        self._tput_last_sample = (now, total_rows)
        return self._tput_ema, 0

    # Per-task display config. The badge label is what shows on the card
    # next to the school code; the color tint differentiates the tasks at
    # a glance when several lanes run in parallel.
    _TASK_BADGE = {
        "enrich":         ("ENR", "bright_cyan"),
        "crawl":          ("CRW", "yellow"),
        "backfill-email": ("EML", "magenta"),
    }

    def refresh_display(self) -> None:
        now_utc = datetime.now(timezone.utc)
        active = _scan_active_processes()
        active_schools = {a["school"] for a in active}
        lock = _read_lock(self.lock_path)
        per_jsonl, totals, n_files = _summarize_jsonl(self.log_dir)
        db_rows = _db_summary()

        # ---- title bar ----
        task_counts: dict[str, int] = {}
        for a in active:
            task_counts[a["task"]] = task_counts.get(a["task"], 0) + 1
        task_summary = (
            " · ".join(f"{n} {t}" for t, n in task_counts.items())
            if task_counts else "idle"
        )
        self.query_one("#title_bar", Static).update(
            Text.from_markup(
                f"⚡ supervisor-claw · live monitor   "
                f"[dim]{now_utc.isoformat(timespec='seconds')}Z · "
                f"refresh {self.refresh_s:.0f}s · {task_summary}[/]"
            )
        )

        # ---- lane cards ----
        for slot in range(self.LANES):
            card = self.query_one(f"#lane_card_{slot}", Static)
            if slot < len(active):
                a = active[slot]
                alive = _pid_alive(a["pid"])
                lane_letter = chr(ord("A") + slot)
                task = a["task"]
                badge_label, tint = self._TASK_BADGE.get(task, ("?", "white"))
                if alive:
                    card.set_class(True, "lane_card")
                    card.set_class(False, "idle")
                    card.set_class(False, "dead")
                else:
                    card.set_class(True, "dead")
                heart = "[bold green]●[/]" if alive else "[bold red]✗[/]"
                school = a["school"]
                pid = a["pid"]

                # Per-task progress shape
                if task == "enrich":
                    j = per_jsonl.get(school, {})
                    cur = j.get("rows", 0)
                    tot = next(
                        (r["total"] for r in db_rows if r["code"] == school), 0
                    )
                    pct = (cur * 100 / tot) if tot else 0
                    line2 = f"[dim]{cur}/{tot}[/]"
                elif task == "backfill-email":
                    done, hits, timeouts, last = _email_backfill_progress(school)
                    tot = _email_backfill_total(school)
                    pct = (done * 100 / tot) if tot else 0
                    extra = []
                    if hits:
                        extra.append(f"[green]✓{hits}[/]")
                    if timeouts:
                        extra.append(f"[yellow]⏱{timeouts}[/]")
                    extra_str = " ".join(extra)
                    line2 = (
                        f"[dim]{done}/{tot}[/]  {extra_str}".rstrip()
                    )
                else:  # crawl — no per-school totals reliable from /proc alone
                    cur = 0
                    tot = 0
                    pct = 0.0
                    line2 = "[dim]running…[/]"

                bar = _bar(pct, width=20)
                color = _bar_color(pct) if pct else "white"
                card.update(Text.from_markup(
                    f"[bold]Lane {lane_letter}[/]  {heart} "
                    f"[bold {tint}]{badge_label}[/] "
                    f"[bold magenta]{school}[/]\n"
                    f"[{color}]{bar}[/]  [bold]{pct:5.1f}%[/]  {line2}\n"
                    f"[dim]pid {pid} · up {_fmt_etime(a['etime_s'])}[/]"
                ))
            else:
                card.set_class(False, "dead")
                card.set_class(True, "idle")
                card.update(Text.from_markup(
                    f"[bold]Lane {chr(ord('A') + slot)}[/]  "
                    f"[dim]· idle ·[/]\n\n[dim]no active claw process[/]"
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

        if self._tput_has_progress and per_min > 0.05:
            tput_line = f"[bold green]{per_min:.1f}[/] advisor/min"
            eta_line = f"[bold]ETA[/] [cyan]{_fmt_eta(eta_s)}[/]"
        elif self._tput_has_progress:
            # progress seen earlier but EMA decayed near zero (long idle)
            tput_line = "[yellow]idle…[/]"
            eta_line = "[dim]ETA —[/]"
        else:
            tput_line = "[dim]warming up…[/]"
            eta_line = "[dim]ETA —[/]"
        self.query_one("#card_throughput", Static).update(Text.from_markup(
            f"[bold dim]THROUGHPUT · ETA[/]\n"
            f"{tput_line}\n"
            f"{eta_line}\n"
            f"[dim]{remaining:,} advisor remaining[/]"
        ))


def run_watch_tui(refresh_s: float = 1.0) -> None:
    """Entry point used by ``claw watch``."""
    init_db()
    EnrichMonitor(refresh_s=refresh_s).run()
