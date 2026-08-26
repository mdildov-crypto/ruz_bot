"""
Диагностический скрипт. Запусти его ПЕРЕД деплоем бота:

    python check_api.py

Он попробует найти твою группу и получить расписание через адреса,
зашитые в ruz_client.py. Если что-то пойдёт не так — скрипт прямо
скажет, что и где нужно поправить.

Как узнать реальные адреса API, если скрипт ошибётся:
1. Открой https://ruz.guz.ru в Chrome/Edge.
2. Нажми F12 -> вкладка "Network" (Сеть) -> фильтр "Fetch/XHR".
3. Введи в поиск на сайте название своей группы.
4. В списке запросов найди тот, что вернул список групп (обычно
   с "search" в адресе) -> вкладка "Response" покажет JSON.
5. Выбери свою группу на сайте -> появится новый запрос, который
   вернул само расписание (обычно со словом "schedule").
6. Скопируй оба адреса (Request URL) и открой ruz_client.py —
   замени пути в methods find_group_id() и get_schedule() на
   реальные, и поля в _normalize_lesson()/find_group_id() под
   реальные названия полей из Response.
"""

import asyncio

from config import settings
from ruz_client import RuzClient, RuzApiError, fetch_full_schedule


async def main():
    print(f"Сайт: {settings.ruz_base_url}")
    print(f"Ищу группу: {settings.group_name!r}\n")

    client = RuzClient()
    try:
        try:
            group_id, label = await client.find_group_id(settings.group_name)
        except RuzApiError as e:
            print("❌ Не удалось найти группу через /ruz/api/search")
            print(f"   Причина: {e}\n")
            print("   Открой инструкцию в шапке этого файла и поправь")
            print("   метод find_group_id() в ruz_client.py")
            return

        print(f"✅ Группа найдена: {label!r} (id={group_id})\n")

        try:
            lessons = await fetch_full_schedule(client, group_id)
        except RuzApiError as e:
            print("❌ Не удалось получить расписание через /ruz/api/schedule/group/{id}")
            print(f"   Причина: {e}\n")
            print("   Поправь метод get_schedule() в ruz_client.py")
            return

        print(f"✅ Расписание получено: {len(lessons)} пар до конца семестра.\n")
        print("Первые несколько пар (проверь, что поля выглядят разумно):\n")
        for l in lessons[:5]:
            print(f"  {l.lesson_date} {l.time_start}-{l.time_end}  {l.subject!r}"
                  f"  [{l.kind}]  {l.teacher!r}  ауд.{l.room} {l.building}")

        if not lessons:
            print("⚠️  Список пуст — либо у группы правда нет пар в этом диапазоне,")
            print("    либо не так распознаются поля ответа (см. _normalize_lesson).")

        print("\nЕсли все поля выше выглядят корректно — можно деплоить бота.")
        print("Если что-то пустое/неправильное — поправь _normalize_lesson() в ruz_client.py")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
