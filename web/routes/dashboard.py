"""관리자 대시보드 — 앱 첫 화면("/").

세 블록을 한눈에 보여준다:
1. 다가오는 알림 타임라인 — scheduled_notifications의 pending 중 향후 30일 이내를
   날짜별로 묶고 D-day 뱃지를 붙인다. 메시지는 parse_mode=HTML로 저장돼 있어
   태그를 제거·요약해 노출한다.
2. 가족 요약 — 활성 인원 수, 30일 내 생일 임박 구성원.
3. 빠른 액션 — 가족/규칙 추가·공지 바로가기.

기존 "/"는 /members로 302 리다이렉트만 했다(main.py). 이 라우터가 대체한다.
"""

import re
from datetime import date, timedelta
from html import unescape
from typing import TypedDict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from shared.config import settings
from shared.dates import replace_year
from shared.db import get_session
from shared.enums import NotificationStatus
from shared.generators._time import now_utc, today_local
from shared.lunar import lunar_to_solar
from shared.models import FamilyMember, ScheduledNotification
from web.auth import verify_admin
from web.templating import templates

router = APIRouter(dependencies=[Depends(verify_admin)])

_HORIZON_DAYS = 30
_SUMMARY_LEN = 60
_TAG_RE = re.compile(r"<[^>]+>")


class TimelineItem(TypedDict):
    time: str
    message: str


class TimelineGroup(TypedDict):
    date: date
    dday: str
    items: list[TimelineItem]


class UpcomingBirthday(TypedDict):
    name: str
    date: date
    dday: str


def _summarize(message: str) -> str:
    """HTML 태그 제거 + 엔티티 복원 + 공백 정리 후 요약 길이로 자른다."""
    text = unescape(_TAG_RE.sub("", message))
    text = " ".join(text.split())
    if len(text) > _SUMMARY_LEN:
        text = text[: _SUMMARY_LEN - 1].rstrip() + "…"
    return text


def _dday_label(target: date, today: date) -> str:
    days = (target - today).days
    return "D-DAY" if days == 0 else f"D-{days}"


def _next_birthday(member: FamilyMember, today: date, horizon: date) -> date | None:
    """구성원의 다음 양력 생일이 [today, horizon]에 들면 그 날짜, 아니면 None.

    양력 생일을 우선하고(구성원 카드 표기와 동일), 없으면 음력을 해당 연도 양력으로
    변환한다. 연말 음력 생일이 이듬해로 넘어가는 경우까지 올해·내년 두 해를 본다.
    """
    for year in (today.year, today.year + 1):
        occ: date | None = None
        if member.birthday_solar:
            occ = replace_year(member.birthday_solar, year)
        elif member.birthday_lunar:
            resolved = lunar_to_solar(year, member.birthday_lunar.month, member.birthday_lunar.day)
            if resolved:
                occ = date(*resolved)
        if occ and today <= occ <= horizon:
            return occ
    return None


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    tz = ZoneInfo(settings.tz)
    today = today_local()
    horizon_date = today + timedelta(days=_HORIZON_DAYS)
    now = now_utc()
    horizon_dt = now + timedelta(days=_HORIZON_DAYS)

    with get_session() as session:
        pending = session.scalars(
            select(ScheduledNotification)
            .where(
                ScheduledNotification.status == NotificationStatus.pending,
                ScheduledNotification.scheduled_at >= now,
                ScheduledNotification.scheduled_at <= horizon_dt,
            )
            .order_by(ScheduledNotification.scheduled_at)
        ).all()

        members = session.scalars(
            select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
        ).all()

        # 날짜별 그룹 — 로컬 타임존 벽시계 기준으로 묶는다.
        timeline: list[TimelineGroup] = []
        current_key: date | None = None
        for n in pending:
            local_dt = n.scheduled_at.astimezone(tz)
            day = local_dt.date()
            if day != current_key:
                timeline.append({"date": day, "dday": _dday_label(day, today), "items": []})
                current_key = day
            timeline[-1]["items"].append(
                {"time": local_dt.strftime("%H:%M"), "message": _summarize(n.message)}
            )

        upcoming_birthdays: list[UpcomingBirthday] = []
        for m in members:
            occ = _next_birthday(m, today, horizon_date)
            if occ:
                upcoming_birthdays.append(
                    {"name": m.name, "date": occ, "dday": _dday_label(occ, today)}
                )
        upcoming_birthdays.sort(key=lambda b: b["date"])

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "timeline": timeline,
            "member_count": len(members),
            "upcoming_birthdays": upcoming_birthdays,
            "today": today,
        },
    )
