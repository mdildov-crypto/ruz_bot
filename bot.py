from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import storage
from config import settings
from diff import diff_schedules
from formatting import format_changes, format_day, format_week
from ruz_client import Lesson, RuzClient, RuzApiError, fetch_full_schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
ruz = RuzClient()


# ---------------------------------------------------------------------------
# Вспомогательное: получить (и закэшировать) id группы
# ---------------------------------------------------------------------------
async def get_group_id() -> str:
    state = storage.load()
    if state.get("group_id"):
        return state["group_id"]

    group_id, label = await ruz.find_group_id(settings.group_name)
    state["group_id"] = group_id
    state["group_label"] = label
    storage.save(state)
    log.info("Группа определена: %s -> id=%s", label, group_id)
    return group_id


async def get_current_lessons() -> list[Lesson]:
    group_id = await get_group_id()
    return await fetch_full_schedule(ruz, group_id)


def _lessons_for_day(lessons: list[Lesson], day: date) -> list[Lesson]:
    iso = day.isoformat()
    return [l for l in lessons if l.lesson_date == iso]


# ---------------------------------------------------------------------------
# Клавиатура с кнопками (чтобы не печатать команды руками)
# ---------------------------------------------------------------------------
BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "📆 Завтра"
BTN_WEEK = "🗓 Эта неделя"
BTN_CHECK = "🔄 Проверить изменения"

RU_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def today_date_label() -> str:
    """Кнопка с сегодняшней датой — пересчитывается каждый раз заново,
    так что всегда показывает актуальное число."""
    d = date.today()
    return f"📌 {d.day} {RU_MONTHS_GEN[d.month - 1]}"


def is_today_date_button(message: Message) -> bool:
    return message.text == today_date_label()


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=today_date_label())],
            [KeyboardButton(text=BTN_CHECK)],
        ],
        resize_keyboard=True,
    )


def week_kb(monday: date) -> InlineKeyboardMarkup:
    """Кнопки ◀ / ▶ под самим расписанием — можно листать сколько угодно недель."""
    prev_monday = (monday - timedelta(weeks=1)).isoformat()
    next_monday = (monday + timedelta(weeks=1)).isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀ Пред. неделя", callback_data=f"week:{prev_monday}"),
                InlineKeyboardButton(text="След. неделя ▶", callback_data=f"week:{next_monday}"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    added_now = storage.add_subscriber(message.chat.id)
    text = (
        "Привет! Я показываю расписание пар группы "
        f"<b>{settings.group_name}</b> и сообщаю, когда сайт что-то меняет.\n\n"
        "Пользуйся кнопками внизу 👇"
    )
    if added_now:
        text += "\n\nТы подписан(а) на уведомления об изменениях в расписании ✅"
    await message.answer(text, reply_markup=main_menu_kb())


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    if storage.add_subscriber(message.chat.id):
        await message.answer("Подписал(а) тебя на уведомления об изменениях ✅")
    else:
        await message.answer("Ты уже подписан(а) 🙂")


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    if storage.remove_subscriber(message.chat.id):
        await message.answer("Отписал(а) от уведомлений об изменениях.")
    else:
        await message.answer("Ты и так не был(а) подписан(а).")


@dp.message(Command("today"))
@dp.message(F.text == BTN_TODAY)
@dp.message(is_today_date_button)
async def cmd_today(message: Message):
    await _send_day(message, date.today())


@dp.message(Command("tomorrow"))
@dp.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message):
    await _send_day(message, date.today() + timedelta(days=1))


async def _send_day(message: Message, day: date):
    try:
        lessons = await get_current_lessons()
    except RuzApiError as e:
        await message.answer(f"⚠️ Не получилось получить расписание: {e}")
        return
    await message.answer(format_day(day, _lessons_for_day(lessons, day)))


@dp.message(Command("week"))
@dp.message(F.text == BTN_WEEK)
async def cmd_week(message: Message):
    await _send_week(message, offset_weeks=0)


@dp.message(Command("nextweek"))
async def cmd_next_week(message: Message):
    await _send_week(message, offset_weeks=1)


async def _send_week(message: Message, offset_weeks: int):
    try:
        lessons = await get_current_lessons()
    except RuzApiError as e:
        await message.answer(f"⚠️ Не получилось получить расписание: {e}")
        return
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)
    await message.answer(format_week(monday, lessons), reply_markup=week_kb(monday))


@dp.callback_query(F.data.startswith("week:"))
async def cb_week(callback: CallbackQuery):
    """Листание недель вперёд/назад по кнопкам под сообщением."""
    monday = date.fromisoformat(callback.data.split(":", 1)[1])
    try:
        lessons = await get_current_lessons()
    except RuzApiError as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    await callback.message.edit_text(format_week(monday, lessons), reply_markup=week_kb(monday))
    await callback.answer()


@dp.message(Command("check"))
@dp.message(F.text == BTN_CHECK)
async def cmd_check(message: Message):
    await message.answer("Проверяю сайт на изменения…")
    changed = await check_for_changes(notify=True)
    if not changed:
        await message.answer("Изменений нет, всё как было 👍")


# ---------------------------------------------------------------------------
# Фоновая проверка изменений
# ---------------------------------------------------------------------------
async def check_for_changes(notify: bool = True) -> bool:
    state = storage.load()
    try:
        new_lessons = await get_current_lessons()
    except RuzApiError as e:
        log.warning("Проверка изменений не удалась: %s", e)
        return False

    old_lessons = [Lesson.from_dict(d) for d in state.get("lessons", {}).values()] \
        if isinstance(state.get("lessons"), dict) else []

    added, removed, changed = diff_schedules(old_lessons, new_lessons)

    # сохраняем новый снимок в любом случае
    state = storage.load()
    state["lessons"] = {l.key(): l.to_dict() for l in new_lessons}
    storage.save(state)

    if not (added or removed or changed):
        return False

    if notify:
        text = format_changes(added, removed, changed)
        for chat_id in state.get("chat_ids", []):
            try:
                await bot.send_message(chat_id, text)
            except Exception as e:  # noqa: BLE001
                log.warning("Не смог отправить сообщение chat_id=%s: %s", chat_id, e)

    return True


async def scheduled_check():
    log.info("Плановая проверка расписания…")
    await check_for_changes(notify=True)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
async def main():
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN (переменная окружения). Смотри .env.example")

    # первичная инициализация: определяем группу и делаем стартовый снимок,
    # чтобы первая же плановая проверка не разослала "изменения" на пустом месте
    try:
        await get_group_id()
        state = storage.load()
        if not state.get("lessons"):
            lessons = await get_current_lessons()
            state["lessons"] = {l.key(): l.to_dict() for l in lessons}
            storage.save(state)
            log.info("Стартовый снимок расписания сохранён (%d пар).", len(lessons))
    except RuzApiError as e:
        log.error(
            "Не удалось инициализировать расписание при старте: %s\n"
            "Бот всё равно запустится, но проверь адрес API (см. README / check_api.py).",
            e,
        )

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(scheduled_check, "interval", minutes=settings.check_interval_minutes)
    scheduler.start()

    log.info("Бот запущен. Проверка расписания каждые %d мин.", settings.check_interval_minutes)
    try:
        await dp.start_polling(bot)
    finally:
        await ruz.close()


if __name__ == "__main__":
    asyncio.run(main())
