"""
Клиент для сайта расписания (ruz.guz.ru).

Сайт — одностраничное приложение (SPA), которое ходит в свой JSON-API:
/api/search (поиск группы по названию) и /api/schedule/group/{id}
(само расписание).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Any

import httpx

from config import settings

log = logging.getLogger("ruz_client")

DATE_FMT = "%Y.%m.%d"


@dataclass(frozen=True)
class Lesson:
    """Одна пара в нормализованном виде."""

    lesson_date: str          # YYYY-MM-DD
    time_start: str           # "09:00"
    time_end: str             # "10:30"
    subject: str
    kind: str                 # лекция / практика / лаба / ...
    teacher: str
    room: str
    building: str
    detail: str = ""          # доп. пометка с сайта (например, подгруппа "1" или "2")

    def key(self) -> str:
        """Уникальный ключ пары — по нему сверяем 'та же это пара или другая'."""
        return f"{self.lesson_date}|{self.time_start}|{self.subject}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Lesson":
        return Lesson(
            lesson_date=d.get("lesson_date", ""),
            time_start=d.get("time_start", ""),
            time_end=d.get("time_end", ""),
            subject=d.get("subject", ""),
            kind=d.get("kind", ""),
            teacher=d.get("teacher", ""),
            room=d.get("room", ""),
            building=d.get("building", ""),
            detail=d.get("detail", ""),
        )


class RuzApiError(RuntimeError):
    pass


class RuzClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ruz_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=20.0,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; schedule-bot/1.0)",
                "Accept": "application/json",
            },
        )

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Поиск групп по названию (может вернуть несколько совпадений)
    # ------------------------------------------------------------------
    async def search_groups(self, term: str) -> list[tuple[str, str]]:
        """Возвращает список (id, label) — все совпадения с сайта по запросу term."""
        try:
            resp = await self._client.get(
                "/api/search",
                params={"term": term, "type": "group"},
            )
        except httpx.HTTPError as e:
            raise RuzApiError(
                "Сайт с расписанием сейчас не отвечает (проблема на его стороне). "
                "Попробуй ещё раз через минуту."
            ) from e
        if resp.status_code != 200:
            raise RuzApiError(
                f"Поиск группы вернул код {resp.status_code}. "
                f"Похоже, адрес /api/search у этого сайта другой — "
                f"проверь через DevTools (см. README)."
            )
        try:
            data = resp.json()
        except ValueError:
            raise RuzApiError(
                "Поиск группы вернул не JSON — сайт отдал HTML вместо "
                "данных. Нужно уточнить реальный адрес API (см. README)."
            )

        candidates = data if isinstance(data, list) else data.get("items", data)
        results: list[tuple[str, str]] = []
        for c in candidates:
            group_id = str(c.get("id") or c.get("groupId") or c.get("value") or "")
            label = str(c.get("label") or c.get("name") or c.get("value") or "")
            if group_id and group_id != "None" and label:
                results.append((group_id, label))
        return results

    async def find_group_id(self, group_name: str) -> tuple[str, str]:
        """
        Возвращает (id_группы, точное_название_как_на_сайте) для точного
        совпадения по названию (или первого результата, если точного нет).
        Бросает RuzApiError, если ничего не нашлось.
        """
        results = await self.search_groups(group_name)
        if not results:
            raise RuzApiError(f"Группа «{group_name}» не найдена на сайте.")

        exact = None
        for group_id, label in results:
            if label.strip().lower() == group_name.strip().lower():
                exact = (group_id, label)
                break
        return exact or results[0]

    # ------------------------------------------------------------------
    # Справочники: факультеты и полный список групп с курсом и привязкой
    # к факультету — по ним строим выбор без единого текстового ввода
    # ------------------------------------------------------------------
    async def get_faculties(self) -> list[dict]:
        try:
            resp = await self._client.get("/api/dictionary/faculties")
        except httpx.HTTPError as e:
            raise RuzApiError(
                "Сайт с расписанием сейчас не отвечает (проблема на его стороне). "
                "Попробуй ещё раз через минуту."
            ) from e
        if resp.status_code != 200:
            raise RuzApiError(f"Справочник факультетов вернул код {resp.status_code}.")
        try:
            return resp.json()
        except ValueError:
            raise RuzApiError("Справочник факультетов пришёл не в JSON-формате.")

    async def get_groups_dictionary(self) -> list[dict]:
        try:
            resp = await self._client.get("/api/dictionary/groups")
        except httpx.HTTPError as e:
            raise RuzApiError(
                "Сайт с расписанием сейчас не отвечает (проблема на его стороне). "
                "Попробуй ещё раз через минуту."
            ) from e
        if resp.status_code != 200:
            raise RuzApiError(f"Справочник групп вернул код {resp.status_code}.")
        try:
            return resp.json()
        except ValueError:
            raise RuzApiError("Справочник групп пришёл не в JSON-формате.")

    # ------------------------------------------------------------------
    # Расписание группы за диапазон дат
    # ------------------------------------------------------------------
    async def get_schedule(
        self, group_id: str, start: date, finish: date
    ) -> list[Lesson]:
        try:
            resp = await self._client.get(
                f"/api/schedule/group/{group_id}",
                params={
                    "start": start.strftime(DATE_FMT),
                    "finish": finish.strftime(DATE_FMT),
                    "lng": 1,
                },
            )
        except httpx.HTTPError as e:
            raise RuzApiError(
                "Сайт с расписанием сейчас не отвечает (проблема на его стороне). "
                "Попробуй ещё раз через минуту."
            ) from e
        if resp.status_code != 200:
            raise RuzApiError(
                f"Запрос расписания вернул код {resp.status_code}. "
                f"Проверь адрес /api/schedule/group/{{id}} через DevTools."
            )
        try:
            raw = resp.json()
        except ValueError:
            raise RuzApiError("Расписание пришло не в JSON-формате.")

        items = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))
        lessons: list[Lesson] = []
        for it in items:
            lesson = self._normalize_lesson(it)
            if lesson:
                lessons.append(lesson)
        lessons.sort(key=lambda x: (x.lesson_date, x.time_start))
        return lessons

    @staticmethod
    def _pick(d: dict, *keys: str, default: str = "") -> str:
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return str(d[k])
        return default

    def _normalize_lesson(self, item: dict[str, Any]) -> Lesson | None:
        try:
            raw_date = self._pick(item, "date", "lessonDate", "day")
            lesson_date = self._to_iso_date(raw_date)
            return Lesson(
                lesson_date=lesson_date,
                time_start=self._pick(item, "beginLesson", "timeStart", "start"),
                time_end=self._pick(item, "endLesson", "timeEnd", "end"),
                subject=self._pick(item, "discipline", "subject", "name"),
                kind=self._pick(item, "kindOfWork", "type", "lessonType"),
                teacher=self._pick(item, "lecturer", "teacher", "fio"),
                room=self._pick(item, "auditorium", "room", "cabinet"),
                building=self._pick(item, "building", "housing", "campus"),
                detail=self._pick(item, "detailInfo", "subGroup", "stream", "streamName", "note"),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог разобрать запись пары: %s (%s)", item, e)
            return None

    @staticmethod
    def _to_iso_date(raw: str) -> str:
        raw = raw.strip()
        if "." in raw:  # dd.mm.yyyy
            d, m, y = raw.split(".")[:3]
            return f"{y}-{int(m):02d}-{int(d):02d}"
        if "-" in raw and len(raw) >= 10:
            return raw[:10]
        return raw


async def fetch_full_schedule(client: RuzClient, group_id: str) -> list[Lesson]:
    """Расписание от сегодняшнего дня до конца осеннего семестра (settings.semester_end)."""
    today = date.today()
    end = settings.semester_end_date()
    if end <= today:
        end = today + timedelta(days=30)
    return await client.get_schedule(group_id, today, end)
