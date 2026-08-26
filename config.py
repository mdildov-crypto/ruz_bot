import os
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # Telegram
    bot_token: str = os.getenv("BOT_TOKEN", "")

    # РУЗ (сайт расписания)
    ruz_base_url: str = os.getenv("RUZ_BASE_URL", "https://ruz.guz.ru")
    group_name: str = os.getenv("GROUP_NAME", "Б.МН.25.Б3")

    # Конец осеннего семестра — до какой даты тянуть расписание.
    # По умолчанию 31 января (с запасом на сессию), можно переопределить в .env
    semester_end: str = os.getenv("SEMESTER_END", "2027-01-31")

    # Как часто проверять сайт на изменения (в минутах)
    check_interval_minutes: int = _get_int("CHECK_INTERVAL_MINUTES", 20)

    # Часовой пояс для показа времени/дат
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")

    # Куда сохранять состояние (подписчики + последний снимок расписания)
    state_file: str = os.getenv("STATE_FILE", "state.json")

    def semester_end_date(self) -> date:
        y, m, d = map(int, self.semester_end.split("-"))
        return date(y, m, d)


settings = Settings()
