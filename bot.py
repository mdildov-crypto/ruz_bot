from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
# Работа с группами: поиск id на сайте (с кэшем) + получение расписания
# ---------------------------------------------------------------------------
async def resolve_group_id(group_name: str) -> str:
    cached = storage.get_group_cache(group_name)
    if cached.get("group_id"):
        return cached["group_id"]
    group_id, label = await ruz.find_group_id(group_name)
    storage.set_group_id(group_name, group_id, label)
    log.info("Группа определена: %s -> id=%s", label, group_id)
    return group_id


async def get_lessons_for_group(group_name: str) -> list[Lesson]:
    group_id = await resolve_group_id(group_name)
    return await fetch_full_schedule(ruz, group_id)


def _lessons_for_day(lessons: list[Lesson], day: date) -> list[Lesson]:
    iso = day.isoformat()
    return [l for l in lessons if l.lesson_date == iso]


def _short_group_label(group_name: str) -> str:
    """Короткая подпись для кнопки выбора группы, например 'Б.МН.25.Б3' -> 'Б3'."""
    return group_name.split(".")[-1] if "." in group_name else group_name


def group_picker_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=g, callback_data=f"group:{g}")]
        for g in settings.group_list()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def require_user_group(message: Message) -> str | None:
    """Возвращает выбранную пользователем группу, либо просит выбрать и возвращает None."""
    group_name = storage.get_user_group(message.chat.id)
    if not group_name:
        await message.answer("Сначала выбери свою группу:", reply_markup=group_picker_kb())
        return None
    return group_name


# ---------------------------------------------------------------------------
# Клавиатура с кнопками (чтобы не печатать команды руками)
# ---------------------------------------------------------------------------
BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "📆 Завтра"
BTN_WEEK = "🗓 Эта неделя"
BTN_CHECK = "🔄 Проверить изменения"
BTN_CHANGE_GROUP = "🔁 Сменить группу"

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
    # Сравниваем по префиксу 📌, а не по точной дате — иначе кнопка
    # перестаёт работать на следующий день, пока клавиатура не обновится.
    return bool(message.text) and message.text.startswith("📌 ")


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=today_date_label())],
            [KeyboardButton(text=BTN_CHECK)],
            [KeyboardButton(text=BTN_CHANGE_GROUP)],
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


class NoteStates(StatesGroup):
    waiting_text = State()


def note_picker_kb(day_lessons: list[Lesson]) -> InlineKeyboardMarkup:
    """Кнопки со списком пар этого дня — выбрать, к какой добавить заметку."""
    buttons = []
    for l in day_lessons:
        label = f"{l.time_start} {l.subject}"
        if len(label) > 40:
            label = label[:37] + "…"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"note:{l.key()}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    added_now = storage.add_subscriber(message.chat.id)
    group_name = storage.get_user_group(message.chat.id)

    if not group_name:
        await message.answer(
            "Привет! Я показываю расписание пар и сообщаю, когда сайт что-то меняет.\n\n"
            "Сначала выбери свою группу:",
            reply_markup=group_picker_kb(),
        )
        return

    text = f"Привет! Твоя группа: <b>{group_name}</b>.\n\nПользуйся кнопками внизу 👇"
    if added_now:
        text += "\n\nТы подписан(а) на уведомления об изменениях в расписании ✅"
    await message.answer(text, reply_markup=main_menu_kb())


@dp.message(Command("group"))
@dp.message(F.text == BTN_CHANGE_GROUP)
async def cmd_change_group(message: Message):
    await message.answer("Выбери свою группу:", reply_markup=group_picker_kb())


@dp.callback_query(F.data.startswith("group:"))
async def cb_group_picked(callback: CallbackQuery):
    group_name = callback.data.split(":", 1)[1]
    storage.set_user_group(callback.message.chat.id, group_name)
    storage.add_subscriber(callback.message.chat.id)
    await callback.message.answer(
        f"Готово! Твоя группа: <b>{group_name}</b>.\n\nПользуйся кнопками внизу 👇",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


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
    group_name = await require_user_group(message)
    if not group_name:
        return
    await _send_day(message, group_name, date.today())


@dp.message(Command("tomorrow"))
@dp.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message):
    group_name = await require_user_group(message)
    if not group_name:
        return
    await _send_day(message, group_name, date.today() + timedelta(days=1))


async def _send_day(message: Message, group_name: str, day: date):
    try:
        lessons = await get_lessons_for_group(group_name)
    except RuzApiError as e:
        await message.answer(f"⚠️ Не получилось получить расписание: {e}")
        return
    day_lessons = _lessons_for_day(lessons, day)
    notes = storage.get_notes(message.chat.id)
    await message.answer(format_day(day, day_lessons, notes), reply_markup=main_menu_kb())
    if day_lessons:
        await message.answer(
            "Добавить заметку к какой-то паре в этот день?",
            reply_markup=note_picker_kb(day_lessons),
        )


@dp.message(Command("week"))
@dp.message(F.text == BTN_WEEK)
async def cmd_week(message: Message):
    group_name = await require_user_group(message)
    if not group_name:
        return
    await _send_week(message, group_name, offset_weeks=0)


@dp.message(Command("nextweek"))
async def cmd_next_week(message: Message):
    group_name = await require_user_group(message)
    if not group_name:
        return
    await _send_week(message, group_name, offset_weeks=1)


async def _send_week(message: Message, group_name: str, offset_weeks: int):
    try:
        lessons = await get_lessons_for_group(group_name)
    except RuzApiError as e:
        await message.answer(f"⚠️ Не получилось получить расписание: {e}")
        return
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)
    notes = storage.get_notes(message.chat.id)
    await message.answer(format_week(monday, lessons, notes), reply_markup=week_kb(monday))


@dp.callback_query(F.data.startswith("week:"))
async def cb_week(callback: CallbackQuery):
    """Листание недель вперёд/назад по кнопкам под сообщением."""
    group_name = storage.get_user_group(callback.message.chat.id)
    if not group_name:
        await callback.answer("Сначала выбери группу через /group", show_alert=True)
        return
    monday = date.fromisoformat(callback.data.split(":", 1)[1])
    try:
        lessons = await get_lessons_for_group(group_name)
    except RuzApiError as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    notes = storage.get_notes(callback.message.chat.id)
    await callback.message.edit_text(format_week(monday, lessons, notes), reply_markup=week_kb(monday))
    await callback.answer()


@dp.callback_query(F.data.startswith("note:"))
async def cb_note_pick(callback: CallbackQuery, state: FSMContext):
    """Пара выбрана — просим текст заметки."""
    lesson_key = callback.data.split(":", 1)[1]
    await state.update_data(lesson_key=lesson_key)
    await state.set_state(NoteStates.waiting_text)
    await callback.message.answer(
        "Напиши текст заметки к этой паре (или отправь /cancel, чтобы отменить):"
    )
    await callback.answer()


@dp.message(NoteStates.waiting_text)
async def note_text_received(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return
    data = await state.get_data()
    lesson_key = data.get("lesson_key")
    storage.set_note(message.chat.id, lesson_key, message.text or "")
    await state.clear()
    await message.answer("Заметка сохранена ✅ Она будет видна вместе с этой парой в расписании.")


@dp.message(Command("check"))
@dp.message(F.text == BTN_CHECK)
async def cmd_check(message: Message):
    group_name = await require_user_group(message)
    if not group_name:
        return
    await message.answer("Проверяю сайт на изменения…")
    changed = await check_group_for_changes(group_name, notify_chat_ids=[message.chat.id])
    if not changed:
        await message.answer("Изменений нет, всё как было 👍")


# ---------------------------------------------------------------------------
# Фоновая проверка изменений (по всем группам, которые кто-то выбрал)
# ---------------------------------------------------------------------------
async def check_group_for_changes(group_name: str, notify_chat_ids: list[int] | None = None) -> bool:
    """Проверяет ОДНУ группу и, если есть подписчики на неё, шлёт им изменения.
    notify_chat_ids, если передан, используется вместо автопоиска подписчиков
    (нужно для ручной команды /check одного пользователя)."""
    try:
        new_lessons = await get_lessons_for_group(group_name)
    except RuzApiError as e:
        log.warning("Проверка изменений (%s) не удалась: %s", group_name, e)
        return False

    old_snapshot = storage.get_group_lessons_snapshot(group_name)
    old_lessons = [Lesson.from_dict(d) for d in old_snapshot.values()]

    added, removed, changed = diff_schedules(old_lessons, new_lessons)
    storage.set_group_lessons_snapshot(group_name, {l.key(): l.to_dict() for l in new_lessons})

    if not old_snapshot:
        # первый раз видим эту группу — просто запоминаем как есть, без уведомлений
        return False

    if not (added or removed or changed):
        return False

    text = format_changes(added, removed, changed)
    chat_ids = notify_chat_ids
    if chat_ids is None:
        state = storage.load()
        user_group = state.get("user_group", {})
        subs = set(state.get("chat_ids", []))
        chat_ids = [int(cid) for cid, g in user_group.items() if g == group_name and int(cid) in subs]

    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог отправить сообщение chat_id=%s: %s", chat_id, e)

    return True


async def scheduled_check():
    log.info("Плановая проверка расписания…")
    state = storage.load()
    groups_in_use = sorted(set(state.get("user_group", {}).values()))
    for group_name in groups_in_use:
        await check_group_for_changes(group_name)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
async def main():
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN (переменная окружения). Смотри .env.example")

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
