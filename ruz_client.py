"""
Клиент для сайта расписания (ruz.guz.ru).

ВАЖНО: сайт — это одностраничное приложение (SPA), которое само по себе
не отдаёт HTML с расписанием, а ходит в свой JSON-API. Ниже реализован
клиент под самый распространённый вариант такого API ("Тандем РУЗ" —
похожая система стоит на многих вузовских сайтах: /ruz/api/search и
/ruz/api/schedule/group/{id}).

Если после запуска check_api.py окажется, что реальные адреса или поля
в ответе другие — их нужно поправить именно здесь (см. комментарии
"ПОДСТРОЙКА" ниже) и в README.md написано, как их найти через
DevTools браузера за 2 минуты.
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
    # ПОДСТРОЙКА №1: поиск группы по названию -> её внутренний id
    # ------------------------------------------------------------------
    async def find_group_id(self, group_name: str) -> tuple[str, str]:
        """
        Возвращает (id_группы, точное_название_как_на_сайте).
        Бросает RuzApiError, если ничего не нашлось или API ответило
        не так, как ожидалось.
        """
        try:
            resp = await self._client.get(
                "/api/search",
                params={"term": group_name, "type": "group"},
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
        if not candidates:
            raise RuzApiError(f"Группа «{group_name}» не найдена на сайте.")

        # ищем точное совпадение по названию, иначе берём первый результат
        exact = None
        for c in candidates:
            label = str(c.get("label") or c.get("name") or c.get("value") or "")
            if label.strip().lower() == group_name.strip().lower():
                exact = c
                break
        chosen = exact or candidates[0]

        group_id = str(chosen.get("id") or chosen.get("groupId") or chosen.get("value"))
        label = str(chosen.get("label") or chosen.get("name") or group_name)
        if not group_id or group_id == "None":
            raise RuzApiError(
                "Не удалось достать id группы из ответа API — формат "
                "ответа отличается от ожидаемого (см. README)."
            )
        return group_id, label

    # ------------------------------------------------------------------
    # ПОДСТРОЙКА №2: расписание группы за диапазон дат
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
        """
        Пытается разобрать запись о паре из разных вероятных форматов
        полей (в разных версиях этого API поля называются по-разному).
        """
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
        if "-" in raw and len(raw) >= 10:  # уже похоже на yyyy-mm-dd (возможно с временем)
            return raw[:10]
        return raw


async def fetch_full_schedule(client: RuzClient, group_id: str) -> list[Lesson]:
    """Расписание от сегодняшнего дня до конца осеннего семестра (settings.semester_end)."""
    today = date.today()
    end = settings.semester_end_date()
    if end <= today:
        end = today + timedelta(days=30)
    return await client.get_schedule(group_id, today, end)
