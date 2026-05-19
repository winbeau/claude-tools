"""Rich-based TUI for the research agent.

Layout inspired by Claude Code / opencode / Gemini CLI:
- One header panel per advisor (name · school · dept · title · email)
- A live-updating Tree showing each iter and its tool calls
- Tool calls show: emoji + name(args) + status icon + summary
- Final summary panel (counts + elapsed)

Public API:
    display = ResearchDisplay(console)
    with display.advisor(advisor, school_name, dept_name) as adv:
        adv.iter_start(i, llm_text, is_final=False)
        node = adv.tool_started(name, args)
        adv.tool_completed(node, ok=True, summary="5 results")
        # ... after the agent loop:
        adv.summary(result)
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree


# --- visual constants ---
TOOL_EMOJI = {
    "search_web": "🔍",
    "read_page": "📄",
    "submit_evaluation": "💬",
    "submit_quota": "🎓",
    "finish": "🏁",
}
TOOL_COLOR = {
    "search_web": "yellow",
    "read_page": "blue",
    "submit_evaluation": "green",
    "submit_quota": "green",
    "finish": "magenta",
}
STATUS_OK = "[green]✓[/]"
STATUS_FAIL = "[red]✗[/]"
STATUS_SKIP = "[yellow]⊘[/]"
STATUS_RUNNING = "[cyan]…[/]"


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
        self.tree = Tree("[bold dim]agent loop[/]")
        self._live = Live(
            self.tree,
            console=self.console,
            refresh_per_second=8,
            transient=False,
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
            text = "[dim](no text)[/]"
        if len(text) > 120:
            text = text[:117] + "…"
        tag = "[bold magenta]final iter[/]" if is_final else f"[bold]iter {i}[/]"
        self.current_iter = self.tree.add(f"{tag} [dim]·[/] {text}")
        self._refresh()

    def tool_started(self, name: str, args: dict) -> Tree:
        if self.current_iter is None:
            self.current_iter = self.tree.add("[bold]?[/]")
        emoji = TOOL_EMOJI.get(name, "•")
        color = TOOL_COLOR.get(name, "white")
        label = f"{STATUS_RUNNING} {emoji} [{color}]{name}[/][dim]({_fmt_args(name, args)})[/]"
        node = self.current_iter.add(label)
        node._cw_name = name  # type: ignore[attr-defined]
        node._cw_args = args  # type: ignore[attr-defined]
        self._refresh()
        return node

    def tool_completed(
        self, node: Tree, *, ok: bool, summary: str = "", skipped: bool = False
    ) -> None:
        name = getattr(node, "_cw_name", "?")
        args = getattr(node, "_cw_args", {})
        emoji = TOOL_EMOJI.get(name, "•")
        color = TOOL_COLOR.get(name, "white")
        status = STATUS_SKIP if skipped else (STATUS_OK if ok else STATUS_FAIL)
        suffix = f"  [dim]{summary}[/]" if summary else ""
        node.label = f"{status} {emoji} [{color}]{name}[/][dim]({_fmt_args(name, args)})[/]{suffix}"
        self._refresh()

    def summary(self, result) -> None:
        elapsed = time.time() - self.start_t
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column()
        table.add_row("iters", str(result.iterations))
        table.add_row("evaluations", f"[green]+{result.evaluations_written}[/]")
        table.add_row("quotas", f"[green]+{result.quotas_written}[/]")
        table.add_row("end", result.finished_reason or "[yellow]—[/]")
        table.add_row("elapsed", f"{elapsed:.1f}s")
        if result.error:
            table.add_row("error", f"[red]{result.error}[/]")
        # stop Live first so the panel goes below the tree
        if self._live is not None:
            self._live.stop()
            self._live = None
        self.console.print(
            Panel(
                table,
                border_style="green" if not result.error else "red",
                title=f"[bold]{self.advisor.name_cn}[/] summary",
                title_align="left",
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
        self.console.print(
            Panel(
                Text.from_markup(
                    f"[bold]🦞 supervisor-claw research[/]\n"
                    f"[dim]targets:[/] [cyan]{n}[/] advisor(s) from [cyan]{school_code}[/]"
                ),
                border_style="cyan",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def run_footer(self, ok: int, failed: int, total_elapsed: float) -> None:
        msg = (
            f"[bold]done[/] · "
            f"[green]ok={ok}[/] · "
            f"[red]failed={failed}[/] · "
            f"[dim]elapsed={total_elapsed:.1f}s[/]"
        )
        self.console.print(Panel(Text.from_markup(msg), border_style="green", box=box.ROUNDED))
