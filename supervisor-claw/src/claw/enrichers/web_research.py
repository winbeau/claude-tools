"""Autonomous research agent for one advisor.

Pattern: DeepSeek (OpenAI-compatible tool calling) drives a loop:
    LLM picks a query → search_web → LLM reads chosen pages → LLM submits
    evaluation / quota rows → submit_report (mandatory) → finish().

Tools exposed to the LLM:
    - search_web(query, k=5)              -> list of {title, url, snippet}
    - read_page(url, max_chars=4000)      -> cleaned page text (no scripts)
    - submit_evaluation(...)              -> insert into evaluation table
    - submit_quota(...)                   -> insert into quota_info table
    - submit_report(...)                  -> write 4 enrichment columns on advisor
    - finish(reason)                      -> end loop

Search backend: cn.bing.com (no login, low captcha rate). If a Playwright
session exists at data/sessions/bing.json, it is reused; otherwise the
context is fresh.

Per advisor: max_iter LLM turns, total time bounded by Playwright timeouts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

from playwright.async_api import BrowserContext, TimeoutError as PWTimeoutError
from selectolax.parser import HTMLParser
from sqlmodel import select

from ..config import get_settings
from ..core.browser import BrowserPool
from ..core.llm import get_client
from ..core.logging import get_logger
from ..models.db import Advisor, Evaluation, QuotaInfo
from ..storage.repo import append_evaluation, append_quota

log = get_logger(__name__)


# Sources we refuse to cite — low information density / aggregator pages.
# Search results from these domains are stripped; read_page returns blocked;
# submit_* rejects them so the LLM is forced to find real primary sources.
BLOCKED_DOMAINS: set[str] = {
    "baike.baidu.com",
    "baike.so.com",
    "baike.sogou.com",
    "wiki.mbalib.com",
    "wiki.eol.cn",
    "zh.wikipedia.org",  # arguably useful but often biographical-only
}


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    d = _domain(url)
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


# ---- Tool schemas (OpenAI function-calling format, supported by DeepSeek) ----

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the public web via Bing for academic / forum information "
                "about a Chinese university advisor. Returns top results with "
                "title, url, snippet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, Chinese OK"},
                    "k": {"type": "integer", "description": "How many results, 3-8", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Fetch a URL and return cleaned text (no scripts/nav). Use to "
                "verify a search snippet or read a longer forum post."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 4000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_evaluation",
            "description": (
                "Record one evaluation / review snippet found about the advisor. "
                "Use only when there is actual evaluation content (not just "
                "biographical or research-page boilerplate). Quote the source URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The evaluation text, <=500 chars"},
                    "source_url": {"type": "string"},
                    "rating": {"type": "number", "description": "Optional 1-5 if a rating is given"},
                },
                "required": ["content", "source_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_quota",
            "description": (
                "Record the advisor's recruitment status / quota for a given "
                "year. Fill in fields only when the source explicitly states them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string", "description": "Original text snippet"},
                    "source_url": {"type": "string"},
                    "year": {"type": "integer"},
                    "degree": {"type": "string", "enum": ["PhD", "MS", "Postdoc"]},
                    "count": {"type": "integer"},
                    "confidence": {"type": "number", "description": "0.0-1.0"},
                },
                "required": ["raw_text", "source_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": (
                "**必须在 finish 之前调用一次**。综合本轮收集的全部证据，"
                "给出对该导师的投递参考结构化判断。证据不足时也要提交（"
                "is_recruiting=null / reputation_tag='unknown' / confidence 给低分）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "is_recruiting": {
                        "type": ["boolean", "null"],
                        "description": "true=明确招生 / false=明确不招 / null=未知",
                    },
                    "recruiting_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "对 is_recruiting 判断的置信度 0-1",
                    },
                    "reputation_tag": {
                        "type": "string",
                        "enum": ["positive", "neutral", "negative", "unknown"],
                        "description": "综合学生评价的标签",
                    },
                    "summary": {
                        "type": "string",
                        "description": "1-2 句话投递参考（≤200 字）",
                    },
                },
                "required": [
                    "is_recruiting",
                    "recruiting_confidence",
                    "reputation_tag",
                    "summary",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the research loop for this advisor. Call submit_report first.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


SYSTEM_PROMPT = """你是导师投递参考助手。被指派调研一位**特定**导师：
1) 评价（知乎/小木虫/一亩三分地/贴吧/博客/学生论坛/导师评价网/排名网站）
2) 招生情况（近年是否招博/硕、数量、方向、加入方式）
3) **最终给出结构化投递参考**（招生信号、风评标签、综合判断）

工作流（每轮选 1-2 个动作，不要并发太多）：
1. search_web 用 1-2 个不同角度的查询（如 "X 导师 评价"、"X 招生 博士"、
   "X 课题组 招生 2026"、"X group recruiting"）
2. read_page 读最相关的 1-2 个页面（优先课题组官网 / 知乎答主 / 学生评价网）
3. **遇到任何相关描述就 submit_evaluation / submit_quota**，confidence 如实写
4. **结束前必须调用 submit_report 一次**，给出 is_recruiting / confidence /
   reputation_tag / summary 四件套（证据不足也要提交，confidence 给低分）
5. 最后调用 finish

身份核对：用 school + dept + 研究方向匹配。同名风险大时降低 confidence
但**仍然 submit**，把判断权交给用户。

【优先来源】
- 课题组独立官网（搜 "{name} {school} 课题组" / "{name} group"）
- 知乎回答 / 专栏（zhihu.com/answer/* / zhihu.com/question/*）
- 学校研究生招生目录、招生简章
- 小木虫、一亩三分地、寄托天下、导师评价网

【禁用源】百度百科 / 360 百科 / 搜狗百科 / 维基百科等聚合页 —— 信息密度
低且容易混入同名误导。这些域名 search_web 已过滤、read_page 会返回
blocked、submit_* 会被拒绝。

【重要规则】
- **宁多勿少**：哪怕只是片段、传闻、间接信息，只要写明 source_url 和 confidence 就 submit
- **必须 submit_report**：不调 submit_report 直接 finish 会被视为失败
- **必须 finish**：未 finish 时 max_iter 切断，所有未 submit 的内容会**永久丢失**
- 真的空手而归：submit_report(is_recruiting=null, recruiting_confidence=0.1,
  reputation_tag='unknown', summary='无可用证据') → finish('no info found')"""


LAST_TURN_NUDGE = """⚠️ 这是你的最后一轮，工具被限定为 submit_evaluation / submit_quota / submit_report / finish。

在这一轮里：
1) **批量 submit_evaluation / submit_quota**：把前几轮看到的所有疑似相关信息
   **一次性提交多个 tool_calls**（评价、招生分别一条 submit_*；confidence 可以低）。
2) **必须调用 submit_report 一次**给出综合判断（即使证据稀薄也要交，给低 confidence）。
3) 然后**必须**调用 finish()。
4) 某个 submit 返回 "duplicate (...)" → **换不同的 source_url** 提交别的发现。
5) 若真的一无所获：submit_report(is_recruiting=null, recruiting_confidence=0.1,
   reputation_tag='unknown', summary='无可用证据') → finish('no info found')。

不 submit_report → 该导师下次会被重新调研（白跑）。"""


# Tools available on the wrap-up turn.
FINAL_TOOL_NAMES: set[str] = {
    "submit_evaluation", "submit_quota", "submit_report", "finish"
}


_VALID_REPUTATION_TAGS = {"positive", "neutral", "negative", "unknown"}


_RECRUIT_KW = re.compile(
    r"(招生|招收|招聘|招博|招硕|名额|指标|保研|推免|欢迎报考|欢迎加入|欢迎咨询|"
    r"recruit\w*|Ph\.?D|PhD|Master|openings|positions)",
    re.IGNORECASE,
)


def _bio_recruit_excerpt(bio_text: str | None, *, window: int = 220) -> str | None:
    """Return a short excerpt around the first recruit-keyword hit in bio_text.

    Heuristic only — no LLM. Caller should drop the result into the agent's
    seed prompt to (a) save a homepage read, (b) bias the agent toward the
    actual recruitment phrasing on that homepage. Returns None on miss.
    """
    if not bio_text:
        return None
    m = _RECRUIT_KW.search(bio_text)
    if not m:
        return None
    start = max(0, m.start() - window)
    end = min(len(bio_text), m.end() + window)
    excerpt = bio_text[start:end].strip().replace("\n", " / ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(bio_text) else ""
    return f"{prefix}{excerpt}{suffix}"


def _prior_evidence_summary(session, advisor_id: int) -> str | None:
    """When an advisor is being re-enriched, surface a one-line summary of
    what we already have so the agent doesn't waste tokens re-deriving it."""
    evs = session.exec(select(Evaluation).where(Evaluation.advisor_id == advisor_id).limit(5)).all()
    qs = session.exec(select(QuotaInfo).where(QuotaInfo.advisor_id == advisor_id).limit(5)).all()
    if not evs and not qs:
        return None
    bits: list[str] = []
    if evs:
        bits.append(f"{len(evs)} 条已存评价")
    if qs:
        bits.append(f"{len(qs)} 条已存招生信号")
    return "（上次调研已有：" + " · ".join(bits) + "，请基于此补全或更新，不要重复同源 URL）"


@dataclass
class AgentResult:
    advisor_id: int
    iterations: int = 0
    evaluations_written: int = 0
    quotas_written: int = 0
    report_submitted: bool = False
    is_recruiting: bool | None = None
    recruiting_confidence: float | None = None
    reputation_tag: str | None = None
    enriched_summary: str | None = None
    finished_reason: str | None = None
    error: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


class ResearchAgent:
    """Runs the agent loop for a single advisor."""

    def __init__(
        self,
        advisor: Advisor,
        school_name: str,
        dept_name: str,
        ctx: BrowserContext,
        session,
        *,
        max_iter: int = 8,
        model: str | None = None,
        view=None,  # AdvisorView | None (rich TUI)
    ) -> None:
        self.advisor = advisor
        self.school_name = school_name
        self.dept_name = dept_name
        self.ctx = ctx
        self.session = session  # SQLModel session
        self.max_iter = max_iter
        self.model = model or get_settings().deepseek_model
        self.client = get_client()
        self.result = AgentResult(advisor_id=advisor.id or -1)
        self.view = view  # research_display.AdvisorView, optional
        # set in the loop; checked in _dispatch to hard-block search/read on
        # the final iter even if DeepSeek calls them.
        self._enforce_final: bool = False

    # ----- tool implementations -----

    async def _tool_search_web(self, query: str, k: int = 5) -> list[dict]:
        url = f"https://cn.bing.com/search?q={quote_plus(query)}"
        page = await self.ctx.new_page()
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            # Bing renders results in li.b_algo
            items = await page.evaluate(
                """
                () => {
                  const out = [];
                  document.querySelectorAll('li.b_algo').forEach(li => {
                    const a = li.querySelector('h2 a');
                    const cap = li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4');
                    if (a) out.push({
                      title: a.innerText.trim(),
                      url: a.href,
                      snippet: cap ? cap.innerText.trim() : ''
                    });
                  });
                  return out;
                }
                """
            )
            # filter blocked domains
            filtered = [r for r in items if not _is_blocked(r.get("url", ""))]
            return filtered[: max(1, min(int(k or 5), 8))]
        except PWTimeoutError:
            log.warning("bing search timed out for %r", query)
            return []
        except Exception as e:
            log.warning("bing search failed (%r): %s", query, e)
            return []
        finally:
            await page.close()

    async def _tool_read_page(self, url: str, max_chars: int = 4000) -> str:
        if _is_blocked(url):
            return (
                f"[blocked source: {_domain(url)} — 百科/聚合类禁用，请改用"
                f"实验室主页/招聘公告/导师评价网/知乎/小木虫等原始来源]"
            )
        page = await self.ctx.new_page()
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            html = await page.content()
        except Exception as e:
            return f"[error fetching {url}: {e}]"
        finally:
            await page.close()
        # clean: drop script/style/nav/header/footer
        tree = HTMLParser(html)
        for sel in ("script", "style", "nav", "header", "footer", "aside"):
            for n in tree.css(sel):
                n.decompose()
        text = (tree.body.text(separator="\n", strip=True) if tree.body else "") or ""
        # collapse whitespace
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        cleaned = "\n".join(lines)
        return cleaned[: max(500, min(int(max_chars or 4000), 8000))]

    def _tool_submit_evaluation(
        self, content: str, source_url: str, rating: float | None = None
    ) -> dict:
        if not content.strip():
            return {"ok": False, "error": "empty content"}
        if _is_blocked(source_url):
            return {
                "ok": False,
                "skipped": True,
                "error": f"blocked source {_domain(source_url)} — find a primary source",
            }
        # dedup: same advisor + same source_url already exists
        existing = self.session.exec(
            select(Evaluation).where(
                (Evaluation.advisor_id == self.advisor.id)
                & (Evaluation.source_url == source_url)
            )
        ).first()
        if existing is not None:
            return {"ok": False, "skipped": True, "error": "duplicate (source_url already submitted)"}
        ev = append_evaluation(
            self.session,
            self.advisor,
            source="web_research",
            source_url=source_url,
            content=content[:2000],
            rating=rating,
        )
        self.result.evaluations_written += 1
        return {"ok": True, "evaluation_id": ev.id}

    def _tool_submit_quota(
        self,
        raw_text: str,
        source_url: str,
        year: int | None = None,
        degree: str | None = None,
        count: int | None = None,
        confidence: float | None = None,
    ) -> dict:
        if _is_blocked(source_url):
            return {
                "ok": False,
                "skipped": True,
                "error": f"blocked source {_domain(source_url)} — find a primary source",
            }
        existing = self.session.exec(
            select(QuotaInfo).where(
                (QuotaInfo.advisor_id == self.advisor.id)
                & (QuotaInfo.source_url == source_url)
                & (QuotaInfo.year == year)
                & (QuotaInfo.degree == degree)
            )
        ).first()
        if existing is not None:
            return {"ok": False, "skipped": True, "error": "duplicate (source_url+year+degree)"}
        q = append_quota(
            self.session,
            self.advisor,
            raw_text=raw_text[:1000],
            year=year,
            degree=degree,
            count=count,
            confidence=confidence,
            extractor="deepseek_agent",
            source_url=source_url,
        )
        self.result.quotas_written += 1
        return {"ok": True, "quota_id": q.id}

    def _tool_submit_report(
        self,
        is_recruiting: bool | None,
        recruiting_confidence: float,
        reputation_tag: str,
        summary: str,
    ) -> dict:
        # normalize / clamp
        try:
            conf = float(recruiting_confidence)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        tag = (reputation_tag or "").strip().lower()
        if tag not in _VALID_REPUTATION_TAGS:
            tag = "unknown"
        summary_clean = (summary or "").strip()[:400]
        if isinstance(is_recruiting, str):
            # tolerate string-encoded booleans/nulls from the LLM
            low = is_recruiting.strip().lower()
            if low in ("true", "yes", "1"):
                is_recruiting = True
            elif low in ("false", "no", "0"):
                is_recruiting = False
            else:
                is_recruiting = None

        # write back to advisor row
        self.advisor.is_recruiting = is_recruiting
        self.advisor.recruiting_confidence = conf
        self.advisor.reputation_tag = tag
        self.advisor.enriched_summary = summary_clean
        self.advisor.last_enriched_at = datetime.utcnow()
        self.session.add(self.advisor)
        self.session.commit()

        self.result.report_submitted = True
        self.result.is_recruiting = is_recruiting
        self.result.recruiting_confidence = conf
        self.result.reputation_tag = tag
        self.result.enriched_summary = summary_clean
        return {
            "ok": True,
            "stored": {
                "is_recruiting": is_recruiting,
                "recruiting_confidence": conf,
                "reputation_tag": tag,
            },
        }

    def _tool_finish(self, reason: str) -> dict:
        self.result.finished_reason = reason
        return {"ok": True}

    # ----- dispatcher -----

    async def _dispatch(self, name: str, args: dict) -> Any:
        # Hard-block search/read on the final iter — DeepSeek occasionally
        # ignores the restricted `tools` list and emits a search_web call
        # anyway. Reject before doing any network work.
        if self._enforce_final and name not in FINAL_TOOL_NAMES:
            return {
                "ok": False,
                "blocked": True,
                "error": (
                    f"final iter only allows submit_evaluation / submit_quota / finish — "
                    f"your {name!r} call was discarded. submit findings or call finish() now."
                ),
            }
        if name == "search_web":
            return await self._tool_search_web(args.get("query", ""), args.get("k", 5))
        if name == "read_page":
            url = args.get("url") or ""
            if not url:
                return {"ok": False, "error": "missing required arg 'url'"}
            return await self._tool_read_page(url, args.get("max_chars", 4000))
        if name == "submit_evaluation":
            content = args.get("content") or ""
            source_url = args.get("source_url") or ""
            if not content or not source_url:
                return {"ok": False, "error": "missing required arg(s): content / source_url"}
            return self._tool_submit_evaluation(content, source_url, args.get("rating"))
        if name == "submit_quota":
            raw_text = args.get("raw_text") or ""
            source_url = args.get("source_url") or ""
            if not raw_text or not source_url:
                return {"ok": False, "error": "missing required arg(s): raw_text / source_url"}
            return self._tool_submit_quota(
                raw_text,
                source_url,
                args.get("year"),
                args.get("degree"),
                args.get("count"),
                args.get("confidence"),
            )
        if name == "submit_report":
            return self._tool_submit_report(
                args.get("is_recruiting"),
                args.get("recruiting_confidence", 0.0),
                args.get("reputation_tag", "unknown"),
                args.get("summary", ""),
            )
        if name == "finish":
            return self._tool_finish(args.get("reason", ""))
        return {"ok": False, "error": f"unknown tool {name}"}

    # ----- seed prompt builder -----

    def _build_user_intro(self) -> str:
        adv = self.advisor
        parts: list[str] = [
            f"调研对象：{adv.name_cn}（{self.school_name} · {self.dept_name}）",
            f"职称：{adv.title or '未知'}",
            f"研究方向：{adv.research_interests_raw or '未知'}",
            f"邮箱：{adv.email or '未知'}",
            f"主页：{adv.homepage or '未知'}",
        ]

        # already-crawled bio: include up to 800 chars so the agent doesn't
        # waste a read_page round-trip on the homepage we already have
        bio = (adv.bio_text or "").strip()
        if bio:
            parts.append("")
            parts.append("【官网 bio 摘要（已爬，无需重读主页）】")
            parts.append(bio[:800] + ("…" if len(bio) > 800 else ""))

        # if bio contains recruit-keyword hit, surface it explicitly
        excerpt = _bio_recruit_excerpt(adv.bio_text)
        if excerpt:
            parts.append("")
            parts.append("【bio 中检出的招生相关片段（启发式）】")
            parts.append(excerpt)

        # incremental re-run hint
        prior = _prior_evidence_summary(self.session, adv.id or -1)
        if prior:
            parts.append("")
            parts.append(prior)

        parts.append("")
        parts.append("请用工具调用开始调研。最后**务必** submit_report + finish。")
        return "\n".join(parts)

    # ----- main loop -----

    async def run(self) -> AgentResult:
        user_intro = self._build_user_intro()
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_intro},
        ]

        # tool sets: full set for exploration; wrap-up set for the last turn
        # (only submit_* + finish; no more search/read so the LLM is forced
        # to either commit findings or end cleanly).
        final_tools = [t for t in TOOLS if t["function"]["name"] in FINAL_TOOL_NAMES]

        i = 0
        rescued = False
        max_iter = self.max_iter
        while i < max_iter:
            self.result.iterations = i + 1
            is_last = i == max_iter - 1
            self._enforce_final = is_last  # block search/read in dispatch
            tools_for_turn = final_tools if is_last else TOOLS
            if is_last and not rescued:
                messages.append({"role": "user", "content": LAST_TURN_NUDGE})
            iter_submit_ok = False  # any submit_* returned ok=True this iter
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools_for_turn,
                    tool_choice="auto",
                    temperature=0.2,
                    max_tokens=800,
                )
            except Exception as e:
                self.result.error = f"deepseek error: {e}"
                log.error("[%s] LLM call failed: %s", self.advisor.name_cn, e)
                break

            msg = resp.choices[0].message
            assistant_dict: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            # surface the LLM's text reasoning when present
            if msg.content:
                log.debug(
                    "[%s] iter=%d llm_text=%s",
                    self.advisor.name_cn, i + 1,
                    msg.content[:200].replace("\n", " "),
                )
            if self.view is not None:
                self.view.iter_start(i + 1, msg.content, is_final=is_last)
            tool_calls_raw = getattr(msg, "tool_calls", None) or []
            if tool_calls_raw:
                assistant_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls_raw
                ]
            messages.append(assistant_dict)

            if not tool_calls_raw:
                # plain assistant message with no tool call → treat as soft-finish
                self.result.finished_reason = "no tool call"
                break

            done = False
            for tc in tool_calls_raw:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.debug(
                    "[%s] iter=%d tool=%s args=%s",
                    self.advisor.name_cn, i + 1, name,
                    {k: (str(v)[:60] + "…") if isinstance(v, str) and len(str(v)) > 60 else v
                     for k, v in args.items()},
                )
                node = self.view.tool_started(name, args) if self.view is not None else None
                result = await self._dispatch(name, args)
                self.result.tool_calls.append({"iter": i + 1, "name": name, "args": args})
                # track submission success for rescue-iter decision
                if name in ("submit_evaluation", "submit_quota", "submit_report") and isinstance(result, dict) and result.get("ok"):
                    iter_submit_ok = True
                # ---- describe outcome for the view ----
                if self.view is not None:
                    summary = _summarize_result(name, args, result)
                    skipped = isinstance(result, dict) and (
                        result.get("skipped") is True or result.get("blocked") is True
                    )
                    ok = (
                        not (isinstance(result, dict) and result.get("ok") is False)
                        and not skipped
                    )
                    self.view.tool_completed(node, ok=ok, summary=summary, skipped=skipped)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False)[:6000],
                    }
                )
                if name == "finish":
                    done = True
            if done:
                break

            # ---- rescue iter: final iter ended with no submit_report (or no
            # successful submission of any kind), and no finish(). Grant ONE
            # more last-iter so the LLM can ship the mandatory report.
            need_rescue = (
                is_last and not rescued and not done
                and (not self.result.report_submitted or not iter_submit_ok)
            )
            if need_rescue:
                rescued = True
                max_iter += 1
                missing_report = not self.result.report_submitted
                rescue_msg = (
                    "⚠️ 你还没调用 submit_report —— 这是必填项。立刻提交综合判断"
                    "（即使证据稀薄也要交，confidence 给低分），然后 finish。"
                    if missing_report
                    else "⚠️ 上一轮所有 submit 都失败或被去重跳过。再给你一次机会："
                    "用**不同的 source_url** 提交剩下的发现，并确保 submit_report，"
                    "然后 finish。"
                )
                messages.append({"role": "user", "content": rescue_msg})
            i += 1

        if not done and not self.result.finished_reason:
            self.result.finished_reason = "max_iter reached"
        return self.result


def _summarize_result(name: str, args: dict, result: Any) -> str:
    """One-line summary of a tool result for the TUI."""
    if name == "search_web":
        return f"{len(result) if isinstance(result, list) else 0} results"
    if name == "read_page":
        if isinstance(result, str) and result.startswith("[blocked"):
            return result[:80]
        if isinstance(result, str) and result.startswith("[error"):
            return result[:80]
        return f"~{len(result) if isinstance(result, str) else 0} chars"
    if name in ("submit_evaluation", "submit_quota"):
        if isinstance(result, dict):
            if result.get("ok"):
                return "saved"
            err = result.get("error", "")
            return f"skip: {err}" if result.get("skipped") else f"fail: {err}"
        return ""
    if name == "submit_report":
        if isinstance(result, dict) and result.get("ok"):
            stored = result.get("stored", {})
            ir = stored.get("is_recruiting")
            ir_s = "招生" if ir is True else ("不招" if ir is False else "未知")
            return f"report saved · {ir_s} · {stored.get('reputation_tag','unknown')}"
        return "report failed"
    if name == "finish":
        return ""
    return ""


async def research_advisor(
    advisor: Advisor,
    school_name: str,
    dept_name: str,
    pool: BrowserPool,
    session,
    *,
    max_iter: int = 8,
    view=None,
) -> AgentResult:
    ctx = await pool.context("bing")
    agent = ResearchAgent(
        advisor, school_name, dept_name, ctx, session, max_iter=max_iter, view=view
    )
    return await agent.run()
