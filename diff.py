from __future__ import annotations

from ruz_client import Lesson


def _identity(lesson: Lesson) -> tuple[str, str, str]:
    """Что считаем 'той же парой' — дата + предмет + тип занятия.
    Если у пары поменялись время/аудитория/препод, это даст 'изменено',
    а не 'удалено+добавлено'."""
    return (lesson.lesson_date, lesson.subject, lesson.kind)


def diff_schedules(
    old: list[Lesson], new: list[Lesson]
) -> tuple[list[Lesson], list[Lesson], list[tuple[Lesson, Lesson]]]:
    """Возвращает (added, removed, changed)."""
    old_map = {_identity(l): l for l in old}
    new_map = {_identity(l): l for l in new}

    old_keys = set(old_map)
    new_keys = set(new_map)

    added = [new_map[k] for k in (new_keys - old_keys)]
    removed = [old_map[k] for k in (old_keys - new_keys)]
    changed = [
        (old_map[k], new_map[k])
        for k in (old_keys & new_keys)
        if old_map[k] != new_map[k]
    ]

    added.sort(key=lambda l: (l.lesson_date, l.time_start))
    removed.sort(key=lambda l: (l.lesson_date, l.time_start))
    changed.sort(key=lambda t: (t[1].lesson_date, t[1].time_start))
    return added, removed, changed
