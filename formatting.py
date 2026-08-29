from __future__ import annotations

from datetime import date, datetime, timedelta

from ruz_client import Lesson

WEEKDAYS_RU = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]

KIND_EMOJI = {
    "лекция": "📘",
    "практика": "📗",
    "лабораторная": "🧪",
    "лаб": "🧪",
    "семинар": "📗",
    "экзамен": "❗",
    "зачет": "❗",
    "зачёт": "❗",
}


def _kind_emoji(kind: str) -> str:
    kind_l = (kind or "").lower()
    for key, emo in KIND_EMOJI.items():
        if key in kind_l:
            return emo
    return "📚"


def _fmt_lesson_line(lesson: Lesson, note: str | None = None) -> str:
    emo = _kind_emoji(lesson.kind)
    time_part = f"{lesson.time_start}–{lesson.time_end}" if lesson.time_start else "—"
    place = " ".join(p for p in [lesson.building, lesson.room] if p)
    lines = [f"{emo} <b>{time_part}</b>  {lesson.subject or '—'}"]
    extra = []
    if lesson.kind:
        extra.append(lesson.kind)
    if lesson.teacher:
        extra.append(lesson.teacher)
    if place:
        extra.append(f"ауд. {place}")
    if extra:
        lines.append("    " + " · ".join(extra))
    if note:
        lines.append(f"    📝 <i>{note}</i>")
    return "\n".join(lines)


def format_day(day: date, lessons: list[Lesson], notes: dict[str, str] | None = None) -> str:
    notes = notes or {}
    weekday = WEEKDAYS_RU[day.weekday()]
    header = f"🗓 <b>{weekday}, {day.strftime('%d.%m.%Y')}</b>"
    if not lessons:
        return f"{header}\n\nПар нет 🎉"
    body = "\n\n".join(_fmt_lesson_line(l, notes.get(l.key())) for l in lessons)
    return f"{header}\n\n{body}"


def format_week(start: date, lessons: list[Lesson], notes: dict[str, str] | None = None) -> str:
    """lessons — уже отфильтрованы под нужную неделю (7 дней от start)."""
    by_day: dict[str, list[Lesson]] = {}
    for l in lessons:
        by_day.setdefault(l.lesson_date, []).append(l)

    chunks = []
    for i in range(7):
        d = start + timedelta(days=i)
        iso = d.isoformat()
        day_lessons = by_day.get(iso, [])
        chunks.append(format_day(d, day_lessons, notes))
    return "\n\n➖➖➖➖➖\n\n".join(chunks)


def format_changes(added: list[Lesson], removed: list[Lesson], changed: list[tuple[Lesson, Lesson]]) -> str:
    parts = ["🔔 <b>В расписании есть изменения!</b>"]

    if removed:
        parts.append("\n<b>❌ Убрано / отменено:</b>")
        for l in removed:
            parts.append("  " + _fmt_lesson_line(l).replace("\n", "\n  "))

    if added:
        parts.append("\n<b>✅ Добавлено:</b>")
        for l in added:
            parts.append("  " + _fmt_lesson_line(l).replace("\n", "\n  "))

    if changed:
        parts.append("\n<b>✏️ Изменено:</b>")
        for old, new in changed:
            date_str = new.lesson_date
            parts.append(f"  {date_str} — {old.subject or new.subject}")
            old_line = f"    было:  {old.time_start}–{old.time_end}, {old.teacher or '—'}, ауд. {old.room or '—'}"
            new_line = f"    стало: {new.time_start}–{new.time_end}, {new.teacher or '—'}, ауд. {new.room or '—'}"
            parts.append(old_line)
            parts.append(new_line)

    return "\n".join(parts)
