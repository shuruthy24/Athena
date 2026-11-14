"""A deliberately small reader for OSM `opening_hours`, enough to ask "is this venue open at 6 pm, 10 pm or
1 am on a Friday night?". It understands "24/7", day lists and ranges (Mo-Fr, Sa,Su), several time spans
(10:00-14:00,17:00-23:00), spans past midnight (18:00-02:00) and rules separated by ";". Anything else
(public holidays, months, "sunset", comments) makes the value unknown, and unknown venues are not counted
as open: an unparsed value never makes a street look busier than the data shows.
"""
from __future__ import annotations

import re

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
# Each band is checked on a Friday night; 01:00 means the early hours of Saturday.
BAND_TIMES = {"18": ("Fr", 18 * 60), "22": ("Fr", 22 * 60), "01": ("Sa", 1 * 60)}
_SPAN = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _days(spec: str) -> set[str] | None:
    out: set[str] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                return None
            i, j = DAYS.index(a), DAYS.index(b)
            out.update(DAYS[i:j + 1] if i <= j else DAYS[i:] + DAYS[:j + 1])
        elif part in DAYS:
            out.add(part)
        else:
            return None
    return out


def parse(value: str | None) -> list[tuple[set[str], int, int]] | None:
    """Return [(days, start_minute, end_minute)] or None when the value is missing or not understood.
    end may exceed 24*60 when a span runs past midnight."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if value == "24/7":
        return [(set(DAYS), 0, 24 * 60)]
    rules = []
    for rule in value.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        if " " in rule:
            day_spec, times = rule.split(" ", 1)
            days = _days(day_spec)
        else:
            days, times = set(DAYS), rule
        if days is None:
            return None
        if times.strip().lower() == "off":  # "Su off": remove those days from earlier rules
            rules = [(d - days, s, e) for d, s, e in rules]
            continue
        for span in times.split(","):
            m = _SPAN.match(span.strip())
            if not m:
                return None
            h1, m1, h2, m2 = map(int, m.groups())
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if end <= start:
                end += 24 * 60
            rules.append((days, start, end))
    return rules or None


def open_at(value: str | None, band: str) -> bool | None:
    """True/False when the hours are understood, None when unknown."""
    rules = parse(value)
    if rules is None:
        return None
    day, minute = BAND_TIMES[band]
    prev = DAYS[(DAYS.index(day) - 1) % 7]
    for days, start, end in rules:
        if day in days and start <= minute < end:
            return True
        if prev in days and end > 24 * 60 and start <= minute + 24 * 60 < end:  # yesterday's late span
            return True
    return False
