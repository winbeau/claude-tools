"""Rich-based TUI for the research agent.

Layout inspired by Claude Code / opencode / Gemini CLI:
- One header panel per advisor (name · school · dept · title · email)
- A live-updating Tree showing each iter and its tool calls
- Tool calls in progress: animated braille spinner + Nerd Font icon
- Tool calls completed: static check/cross/skip glyph + name(args) + summary
- Final summary panel per advisor (counts + elapsed)

Public API:
    display = ResearchDisplay(console)
    with display.advisor(advisor, school_name, dept_name) as adv:
        adv.iter_start(i, llm_text, is_final=False)
        node = adv.tool_started(name, args)
        adv.tool_completed(node, ok=True, summary="5 results")
        adv.summary(result)
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.tree import Tree


# --- visual constants (Nerd Font glyphs; requires a Nerd Font installed) ---
# We use Font Awesome / MDI subsets that ship in every Nerd Font release.
NF_SEARCH = ""       # nf-fa-search
NF_FILE = ""          # nf-fa-file_text
NF_COMMENT = ""       # nf-fa-comment
NF_GRADCAP = ""       # nf-fa-graduation_cap
NF_FLAG = ""          # nf-fa-flag
NF_CHECK = ""         # nf-fa-check
NF_TIMES = ""         # nf-fa-times
NF_BAN = ""           # nf-fa-ban
NF_DIAMOND = ""       # nf-fa-money (looks like a diamond shape — header mark)
NF_PLAY = ""          # nf-fa-play (header alt)

TOOL_ICON = {
    "search_web": NF_SEARCH,
    "read_page": NF_FILE,
    "submit_evaluation": NF_COMMENT,
    "submit_quota": NF_GRADCAP,
    "submit_report": NF_DIAMOND,
    "finish": NF_FLAG,
}
TOOL_COLOR = {
    "search_web": "yellow",
    "read_page": "cyan",
    "submit_evaluation": "green",
    "submit_quota": "magenta",
    "submit_report": "bright_magenta",
    "finish": "bright_green",
}
STATUS_OK = f"[green]{NF_CHECK}[/]"
STATUS_FAIL = f"[red]{NF_TIMES}[/]"
STATUS_SKIP = f"[yellow]{NF_BAN}[/]"
HEADER_MARK = "❯"  # also a single powerline glyph, matches typical zsh prompts


def silence_loggers() -> None:
    """Mute external loggers that would scroll above the Live region and cause
    visible re-paints. Call once before opening Live."""
    import logging
    for name in (
        "httpx", "httpcore", "openai", "openai._base_client",
        "playwright", "asyncio", "urllib3", "selenium",
        "claw",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def _fmt_args(name: str, args: dict) -> str:
    """Compact arg display per tool."""
    if name == "search_web":
        q = args.get("query", "")
        if len(q) > 60:
            q = q[:57] + "…"
        return f"'{q}'"
    if name == "read_page":
        u = args.get("url", "")
        if len(u) > 70:
            u = u[:67] + "…"
        return u
    if name == "submit_evaluation":
        c = (args.get("content") or "")[:50]
        r = args.get("rating")
        return f"rating={r} · {c}…"
    if name == "submit_quota":
        deg = args.get("degree") or "?"
        yr = args.get("year") or "?"
        cnt = args.get("count")
        conf = args.get("confidence")
        return f"{deg} {yr}" + (f" ×{cnt}" if cnt else "") + (f" conf={conf}" if conf else "")
    if name == "submit_report":
        ir = args.get("is_recruiting")
        ir_s = "招生" if ir is True else ("不招" if ir is False else "未知")
        tag = args.get("reputation_tag", "?")
        conf = args.get("recruiting_confidence")
        summ = (args.get("summary") or "")[:30]
        return f"{ir_s} · {tag} · conf={conf} · {summ}…"
    if name == "finish":
        return repr(args.get("reason", ""))[:60]
    return ", ".join(f"{k}={v!r}" for k, v in args.items())[:80]


class AdvisorView:
    """Per-advisor live tree manager."""

    def __init__(self, console: Console, advisor, school: str, dept: str) -> None:
        self.console = console
        self.advisor = advisor
        self.school = school
        self.dept = dept
        self.tree: Tree | None = None
        self.current_iter: Tree | None = None
        self.start_t = time.time()
        self._live: Live | None = None

    # --- lifecycle ---

    def __enter__(self) -> "AdvisorView":
        self._print_header()
        self.tree = Tree(Text.from_markup("[bold dim]agent loop[/]"))
        # high refresh keeps the spinner smooth; redirect captures stray prints
        # (e.g. asyncio warnings) into the Live region so they don't push the
        # tree down and cause ghosting.
        self._live = Live(
            self.tree,
            console=self.console,
            refresh_per_second=15,
            transient=False,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self._live.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    # --- event hooks ---

    def iter_start(self, i: int, llm_text: str | None, *, is_final: bool = False) -> None:
        text = (llm_text or "").strip().replace("\n", " ")
        if not text:
            text = "[dim italic](no reasoning)[/]"
        if len(text) > 120:
            text = text[:117] + "…"
        tag = (
            "[bold bright_magenta]final[/]"
            if is_final
            else f"[bold bright_cyan]iter {i}[/]"
        )
        self.current_iter = self.tree.add(Text.from_markup(f"{tag} [dim]·[/] {text}"))
        self._refresh()

    def tool_started(self, name: str, args: dict) -> Tree:
        """Add a tree node with an animated spinner for the in-flight tool.
        The Spinner advances under Live's refresh tick (no manual updates)."""
        if self.current_iter is None:
            self.current_iter = self.tree.add(Text.from_markup("[bold]?[/]"))
        icon = TOOL_ICON.get(name, "•")
        color = TOOL_COLOR.get(name, "white")
        label_text = Text.from_markup(
            f"[{color}]{icon}[/]  [bold]{name}[/][dim]({_fmt_args(name, args)})[/]"
        )
        spinner = Spinner("dots", text=label_text, style=color, speed=1.2)
        node = self.current_iter.add(spinner)
        node._cw_name = name  # type: ignore[attr-defined]
        node._cw_args = args  # type: ignore[attr-defined]
        self._refresh()
        return node

    def tool_completed(
        self, node: Tree, *, ok: bool, summary: str = "", skipped: bool = False
    ) -> None:
        name = getattr(node, "_cw_name", "?")
        args = getattr(node, "_cw_args", {})
        icon = TOOL_ICON.get(name, "•")
        color = TOOL_COLOR.get(name, "white")
        status = STATUS_SKIP if skipped else (STATUS_OK if ok else STATUS_FAIL)
        suffix = f"  [dim]{summary}[/]" if summary else ""
        node.label = Text.from_markup(
            f"{status}  [{color}]{icon}[/]  [bold]{name}[/][dim]({_fmt_args(name, args)})[/]{suffix}"
        )
        self._refresh()

    def summary(self, result) -> None:
        elapsed = time.time() - self.start_t
        table = Table(box=None, show_header=False, padding=(0, 1), expand=False)
        table.add_column(style="dim", justify="right")
        table.add_column()
        table.add_row("iters", str(result.iterations))
        table.add_row("evaluations", f"[green]+{result.evaluations_written}[/]")
        table.add_row("quotas", f"[green]+{result.quotas_written}[/]")
        table.add_row("end", result.finished_reason or "[dim]—[/]")
        table.add_row("elapsed", f"{elapsed:.1f}s")
        if result.error:
            table.add_row("error", f"[red]{result.error}[/]")
        # stop Live first so the panel goes below the tree
        if self._live is not None:
            self._live.stop()
            self._live = None
        # blank line so the panel doesn't collide with the last tree row
        self.console.print()
        self.console.print(
            Panel(
                table,
                border_style="green" if not result.error else "red",
                title=Text.from_markup(f"[bold]{self.advisor.name_cn}[/] · summary"),
                title_align="left",
                box=box.ROUNDED,
                padding=(0, 1),
                expand=False,
            )
        )

    # --- internals ---

    def _print_header(self) -> None:
        a = self.advisor
        header = Text()
        header.append(f"{a.name_cn}", style="bold cyan")
        header.append(f"  ·  {self.school}", style="white")
        header.append(f"  ·  {self.dept}", style="dim")
        header.append("\n")
        meta = []
        if a.title:
            meta.append(f"[white]{a.title}[/]")
        if a.email:
            meta.append(f"[dim]{a.email}[/]")
        if a.homepage:
            hp = a.homepage if len(a.homepage) < 60 else a.homepage[:57] + "…"
            meta.append(f"[blue underline]{hp}[/]")
        header.append(Text.from_markup("  ·  ".join(meta)))
        if a.research_interests_raw:
            header.append("\n")
            ri = a.research_interests_raw
            if len(ri) > 90:
                ri = ri[:87] + "…"
            header.append(Text.from_markup(f"[dim italic]{ri}[/]"))
        self.console.print(
            Panel(header, border_style="cyan", padding=(0, 1), expand=True, box=box.ROUNDED)
        )

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.tree)


class ResearchDisplay:
    def __init__(self, console: Console) -> None:
        self.console = console

    @contextmanager
    def advisor(self, advisor, school: str, dept: str):
        view = AdvisorView(self.console, advisor, school, dept)
        with view as v:
            yield v

    def run_header(self, n: int, school_code: str) -> None:
        body = Text.from_markup(
            f"[bold bright_cyan]{HEADER_MARK} supervisor-claw[/] "
            f"[dim]·[/] [bold]research[/]\n"
            f"[dim]targets[/]  [white]{n}[/] [dim]advisor(s) from[/] "
            f"[bold cyan]{school_code}[/]"
        )
        self.console.print(
            Panel(
                body,
                border_style="bright_cyan",
                box=box.HEAVY,
                padding=(0, 2),
                expand=False,
            )
        )

    def run_footer(self, ok: int, failed: int, total_elapsed: float) -> None:
        parts = [f"[bold]done[/]", f"[green]{NF_CHECK} ok={ok}[/]"]
        if failed:
            parts.append(f"[red]{NF_TIMES} failed={failed}[/]")
        parts.append(f"[dim]{total_elapsed:.1f}s[/]")
        self.console.print()
        self.console.print(
            Panel(
                Text.from_markup("  ·  ".join(parts)),
                border_style="bright_green" if not failed else "yellow",
                box=box.HEAVY,
                padding=(0, 2),
                expand=False,
            )
        )
