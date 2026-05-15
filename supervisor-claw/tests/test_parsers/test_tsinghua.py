"""Tsinghua CS adapter: real-fixture-based parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw.adapters.tsinghua import TsinghuaAdapter
from claw.core.parser_utils import extract_email

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tsinghua"
LIST_URL = "https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm"
PROFILE_URL = "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"


@pytest.fixture
def adapter() -> TsinghuaAdapter:
    return TsinghuaAdapter()


def test_parse_list_basic(adapter: TsinghuaAdapter) -> None:
    html = (FIX / "list.html").read_text(encoding="utf-8")
    items = adapter.parse_list(html, LIST_URL)

    # we expect many advisors on the list page
    assert len(items) > 50, f"expected >50 advisors, got {len(items)}"

    # all items should have a name and profile_url
    assert all(it.name_cn for it in items)
    assert all(it.profile_url and it.profile_url.startswith("http") for it in items)

    # at least 90% should have a valid email
    with_email = [it for it in items if it.email]
    assert len(with_email) / len(items) > 0.9

    # spot check known advisor
    fengjh = next((it for it in items if it.name_cn == "冯建华"), None)
    assert fengjh is not None
    assert fengjh.email == "fengjh@tsinghua.edu.cn"
    assert fengjh.title and "教授" in fengjh.title
    assert fengjh.profile_url.endswith("/info/1111/3490.htm")


def test_parse_profile_basic(adapter: TsinghuaAdapter) -> None:
    from claw.adapters.base import ListItem

    html = (FIX / "profile.html").read_text(encoding="utf-8")
    item = ListItem(
        name_cn="冯建华",
        profile_url=PROFILE_URL,
        title="教授",
        email="fengjh@tsinghua.edu.cn",
    )
    partial = adapter.parse_profile(html, PROFILE_URL, item)

    assert partial.name_cn == "冯建华"
    assert partial.email == "fengjh@tsinghua.edu.cn"
    assert partial.homepage == PROFILE_URL
    assert partial.bio_text and len(partial.bio_text) > 50


def test_email_deobfuscation() -> None:
    cases = [
        ("zhangsan@tsinghua.edu.cn", "zhangsan@tsinghua.edu.cn", False),
        ("zhangsan [at] tsinghua [dot] edu [dot] cn", "zhangsan@tsinghua.edu.cn", True),
        ("zhang.san (AT) pku (DOT) edu (DOT) cn", "zhang.san@pku.edu.cn", True),
        ("no email here", None, False),
    ]
    for raw, expected_email, expected_obf in cases:
        email, obf = extract_email(raw)
        assert email == expected_email, f"got {email!r} for {raw!r}"
        assert obf == expected_obf, f"obf mismatch for {raw!r}"
