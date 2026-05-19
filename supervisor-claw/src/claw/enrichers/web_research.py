"""Autonomous research agent for one advisor.

Pattern: DeepSeek (OpenAI-compatible tool calling) drives a loop:
    LLM picks a query → search_web → LLM reads chosen pages → LLM submits
    evaluation / quota rows → finish().

Tools exposed to the LLM:
    - search_web(query, k=5)              -> list of {title, url, snippet}
    - read_page(url, max_chars=4000)      -> cleaned page text (no scripts)
    - submit_evaluation(...)              -> insert into evaluation table
    - submit_quota(...)                   -> insert into quota_info table
    - finish(reason)                      -> end loop

Search backend: cn.bing.com (no login, low captcha rate). If a Playwright
session exists at data/sessions/bing.json, it is reused; otherwise the
context is fresh.

Per advisor: max_iter LLM turns, total time bounded by Playwright timeouts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import BrowserContext, TimeoutError as PWTimeoutError
from selectolax.parser import HTMLParser

from ..config import get_settings
from ..core.browser import BrowserPool
from ..core.llm import get_client
from ..core.logging import get_logger
from ..models.db import Advisor
from ..storage.repo import append_evaluation, append_quota

log = get_logger(__name__)


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
            "name": "finish",
            "description": "End the research loop for this advisor.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


SYSTEM_PROMPT = """你是导师调研助手。被指派调研一位**特定**导师的：
1) 评价（知乎/小木虫/一亩三分地/贴吧/博客/学生论坛/排名网站）
2) 招生情况（近年是否招博/硕、数量、方向、加入方式）

策略（每轮选 1-2 个动作，不要并发太多）：
1. search_web 用 1-2 个不同角度的查询（如 "X 导师 评价"、"X 招生 博士"）
2. read_page 读最相关的 1-2 个页面
3. **遇到任何相关描述就 submit**：评价提到本人 → submit_evaluation；
   提到招生信号 → submit_quota。confidence 如实写（0.3 / 0.6 / 0.9）
4. 调用 finish 结束

身份核对：用 school + dept + 研究方向匹配。同名风险大时降低 confidence
但**仍然 submit**，把判断权交给用户。

【重要规则】
- **宁多勿少**：哪怕只是片段、传闻、间接信息，只要写明 source_url 和 confidence 就 submit
- **必须 finish**：未 finish 时 max_iter 切断，所有未 submit 的内容会**永久丢失**
- 真的空手而归就 finish('no info found')，这也是合法收尾"""


LAST_TURN_NUDGE = """⚠️ 这是你的最后一轮。立刻执行：
1) 把你前几轮看到的、所有疑似相关的信息全部 submit_evaluation / submit_quota（confidence 可以低，但不要不写）
2) 然后必须调用 finish()
不 finish 的话所有结果丢失。"""


@dataclass
class AgentResult:
    advisor_id: int
    iterations: int = 0
    evaluations_written: int = 0
    quotas_written: int = 0
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
            return items[: max(1, min(int(k or 5), 8))]
        except PWTimeoutError:
            log.warning("bing search timed out for %r", query)
            return []
        except Exception as e:
            log.warning("bing search failed (%r): %s", query, e)
            return []
        finally:
            await page.close()

    async def _tool_read_page(self, url: str, max_chars: int = 4000) -> str:
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

    def _tool_finish(self, reason: str) -> dict:
        self.result.finished_reason = reason
        return {"ok": True}

    # ----- dispatcher -----

    async def _dispatch(self, name: str, args: dict) -> Any:
        if name == "search_web":
            return await self._tool_search_web(args.get("query", ""), args.get("k", 5))
        if name == "read_page":
            return await self._tool_read_page(args["url"], args.get("max_chars", 4000))
        if name == "submit_evaluation":
            return self._tool_submit_evaluation(
                args["content"], args["source_url"], args.get("rating")
            )
        if name == "submit_quota":
            return self._tool_submit_quota(
                args["raw_text"],
                args["source_url"],
                args.get("year"),
                args.get("degree"),
                args.get("count"),
                args.get("confidence"),
            )
        if name == "finish":
            return self._tool_finish(args.get("reason", ""))
        return {"ok": False, "error": f"unknown tool {name}"}

    # ----- main loop -----

    async def run(self) -> AgentResult:
        user_intro = (
            f"调研对象：{self.advisor.name_cn}（{self.school_name} · {self.dept_name}）\n"
            f"职称：{self.advisor.title or '未知'}\n"
            f"研究方向：{self.advisor.research_interests_raw or '未知'}\n"
            f"邮箱：{self.advisor.email or '未知'}\n"
            f"主页：{self.advisor.homepage or '未知'}\n\n"
            f"请用工具调用开始调研。"
        )
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_intro},
        ]

        for i in range(self.max_iter):
            self.result.iterations = i + 1
            # final-turn nudge: append a user message coercing finish
            if i == self.max_iter - 1:
                messages.append({"role": "user", "content": LAST_TURN_NUDGE})
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
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
                log.info(
                    "[%s] iter=%d llm_text=%s",
                    self.advisor.name_cn, i + 1,
                    msg.content[:200].replace("\n", " "),
                )
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
                log.info(
                    "[%s] iter=%d tool=%s args=%s",
                    self.advisor.name_cn, i + 1, name,
                    {k: (str(v)[:60] + "…") if isinstance(v, str) and len(str(v)) > 60 else v
                     for k, v in args.items()},
                )
                result = await self._dispatch(name, args)
                self.result.tool_calls.append({"iter": i + 1, "name": name, "args": args})
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
        else:
            self.result.finished_reason = "max_iter reached"

        return self.result


async def research_advisor(
    advisor: Advisor,
    school_name: str,
    dept_name: str,
    pool: BrowserPool,
    session,
    *,
    max_iter: int = 8,
) -> AgentResult:
    ctx = await pool.context("bing")
    agent = ResearchAgent(
        advisor, school_name, dept_name, ctx, session, max_iter=max_iter
    )
    return await agent.run()
