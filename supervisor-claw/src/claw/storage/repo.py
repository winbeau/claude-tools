"""Repository: idempotent upserts for advisor / department / appointment / snapshot."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from ..models.db import (
    Advisor,
    Appointment,
    Department,
    EnrichmentTrace,
    Evaluation,
    QuotaInfo,
    School,
    SourceSnapshot,
    session_scope,
)
from ..models.pydantic_models import AdvisorPartial


def upsert_school(s: Session, code: str, name_cn: str, name_en: str | None = None) -> School:
    obj = s.exec(select(School).where(School.code == code)).first()
    if obj is None:
        obj = School(code=code, name_cn=name_cn, name_en=name_en)
        s.add(obj)
        s.commit()
        s.refresh(obj)
    return obj


def upsert_department(
    s: Session, school: School, code: str, name_cn: str, list_url: str | None = None
) -> Department:
    obj = s.exec(
        select(Department).where(
            (Department.school_id == school.id) & (Department.code == code)
        )
    ).first()
    if obj is None:
        obj = Department(
            school_id=school.id,
            code=code,
            name_cn=name_cn,
            faculty_list_url=list_url,
        )
        s.add(obj)
        s.commit()
        s.refresh(obj)
    else:
        if list_url and obj.faculty_list_url != list_url:
            obj.faculty_list_url = list_url
            s.add(obj)
            s.commit()
    return obj


def upsert_advisor(
    s: Session, school: School, partial: AdvisorPartial
) -> Advisor:
    q = select(Advisor).where(
        (Advisor.school_id == school.id)
        & (Advisor.name_cn == partial.name_cn)
        & (Advisor.email == partial.email)
    )
    obj = s.exec(q).first()
    if obj is None:
        obj = Advisor(
            school_id=school.id,
            name_cn=partial.name_cn,
            name_en=partial.name_en,
            title=partial.title,
            gender=partial.gender,
            homepage=partial.homepage,
            email=partial.email,
            email_obfuscated=partial.email_obfuscated,
            phone=partial.phone,
            photo_url=partial.photo_url,
            bio_text=partial.bio_text,
            research_interests_raw=partial.research_interests_csv(),
            is_recruiting=partial.is_recruiting,
            raw_quota_text=partial.raw_quota_text,
            source_url=partial.source_url,
        )
        s.add(obj)
        s.commit()
        s.refresh(obj)
    else:
        # merge non-empty fields
        changed = False
        for fld in (
            "name_en", "title", "gender", "homepage", "phone", "photo_url",
            "bio_text", "raw_quota_text", "source_url",
        ):
            new = getattr(partial, fld, None)
            if new and getattr(obj, fld) != new:
                setattr(obj, fld, new)
                changed = True
        ri = partial.research_interests_csv()
        if ri and obj.research_interests_raw != ri:
            obj.research_interests_raw = ri
            changed = True
        if partial.is_recruiting is not None and obj.is_recruiting != partial.is_recruiting:
            obj.is_recruiting = partial.is_recruiting
            changed = True
        if partial.email_obfuscated and not obj.email_obfuscated:
            obj.email_obfuscated = True
            changed = True
        if changed:
            obj.last_updated = datetime.utcnow()
            s.add(obj)
            s.commit()
    return obj


def link_department(s: Session, advisor: Advisor, dept: Department, role: str | None = None) -> None:
    existing = s.exec(
        select(Appointment).where(
            (Appointment.advisor_id == advisor.id)
            & (Appointment.department_id == dept.id)
        )
    ).first()
    if existing is None:
        s.add(Appointment(advisor_id=advisor.id, department_id=dept.id, role=role))
        s.commit()


def record_snapshot(s: Session, url: str, sha256: str, gzip_path: str) -> SourceSnapshot:
    existing = s.exec(
        select(SourceSnapshot).where(
            (SourceSnapshot.url == url) & (SourceSnapshot.sha256 == sha256)
        )
    ).first()
    if existing is not None:
        return existing
    snap = SourceSnapshot(url=url, sha256=sha256, gzip_path=gzip_path)
    s.add(snap)
    s.commit()
    s.refresh(snap)
    return snap


def append_quota(
    s: Session,
    advisor: Advisor,
    raw_text: str,
    *,
    degree: str | None = None,
    year: int | None = None,
    count: int | None = None,
    confidence: float | None = None,
    extractor: str = "regex",
    source_url: str | None = None,
) -> QuotaInfo:
    q = QuotaInfo(
        advisor_id=advisor.id,
        raw_text=raw_text,
        degree=degree,
        year=year,
        count=count,
        confidence=confidence,
        extractor=extractor,
        source_url=source_url,
    )
    s.add(q)
    s.commit()
    s.refresh(q)
    return q


def append_evaluation(
    s: Session,
    advisor: Advisor,
    *,
    source: str,
    content: str,
    source_url: str | None = None,
    rating: float | None = None,
    posted_at: datetime | None = None,
) -> Evaluation:
    ev = Evaluation(
        advisor_id=advisor.id,
        source=source,
        source_url=source_url,
        content=content,
        rating=rating,
        posted_at=posted_at,
    )
    s.add(ev)
    s.commit()
    s.refresh(ev)
    return ev


def append_trace(
    s: Session,
    advisor: Advisor,
    *,
    run_id: str,
    step_idx: int,
    kind: str,
    label: str,
    detail: str,
) -> EnrichmentTrace:
    row = EnrichmentTrace(
        advisor_id=advisor.id,
        run_id=run_id,
        step_idx=step_idx,
        kind=kind,
        label=label,
        detail=detail,
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


__all__ = [
    "session_scope",
    "upsert_school",
    "upsert_department",
    "upsert_advisor",
    "link_department",
    "record_snapshot",
    "append_quota",
    "append_evaluation",
]
