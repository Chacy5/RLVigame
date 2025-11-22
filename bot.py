import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from dotenv import load_dotenv

# ------------------------
# НАСТРОЙКИ ЛОГИРОВАНИЯ
# ------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ------------------------
# РАБОТА С ФАЙЛОМ users.json
# ------------------------
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        with USERS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Не удалось прочитать users.json: {e}")
        return {}


def save_users(users: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with USERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить users.json: {e}")


def get_or_create_user(user_id: int) -> dict:
    users = load_users()
    uid = str(user_id)

    if uid not in users:
        # базовая структура, совместимая со старым users.json 
        users[uid] = {
            "tokens": {},          # твои старые токены (ORDER / LOG / FIX / HYDR и т.п.)
            "rp": 0,               # очки (репутация/рейтинг)
            "bp_level": 0,         # уровень "батл-пасса" / прогресса
            # новые поля под эту игру
            "coins": 0,
            "lootboxes": {         # инвентарь лутбоксов
                "common": 0,
                "uncommon": 0,
                "rare": 0,
                "epic": 0,
                "legendary": 0,
            },
            "last_daily": None,    # дата, когда последний раз выдавали дейлики
        }
        save_users(users)

    return users[uid]


def update_user(user_id: int, updater):
    """
    Удобная обёртка: updater — функция, которая принимает user_dict и может его менять.
    После этого мы сохраняем весь users.json.
    """
    users = load_users()
    uid = str(user_id)
    user = users.get(uid) or get_or_create_user(user_id)
    updater(user)
    users[uid] = user
    save_users(users)


# ------------------------
# ТЕКСТЫ И КЛАВИАТУРА
# ------------------------

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🎮 Профиль"),
        ],
        [
            KeyboardButton(text="📅 Дейлики"),
            KeyboardButton(text="🎁 Лутбоксы"),
        ],
        [
            KeyboardButton(text="🗺 Карта"),
        ],
    ],
    resize_keyboard=True,
)

WELCOME_TEXT = (
    "Привет! Это твоя личная лайф-RPG.\n\n"
    "Я буду выдавать тебе задачи, монеты и лутбоксы.\n"
    "Всё прогресс хранится в файле data/users.json.\n\n"
    "Используй кнопки ниже или команды:\n"
    "• /profile — твой профиль\n"
    "• /daily — получить ежедневные задачи (заглушка)\n"
    "• /lootbox — открыть лутбокс (заглушка)\n"
    "• /map — посмотреть карту прогресса (заглушка)"
)

# Примеры наборов задач/миниквестов — пока просто списки строк.
SMALL_TASKS = [
    "Заправить кровать",
    "Протереть стол",
    "Умыться / снять макияж",
    "Проветрить комнату",
    "Вытереть пыль в одной зоне",
]

MEDIUM_TASKS = [
    "Помыть всю посуду",
    "Прогулка 20 минут",
    "15 минут фокусной работы",
]

BIG_TASKS = [
    "1 час уборки без перерыва",
    "1 час плотной работы над проектом",
]

# ------------------------
# ИНИЦИАЛИЗАЦИЯ AIOGRAM
# ------------------------

dp = Dispatcher()


# ------------------------
# ХЕНДЛЕРЫ
# ------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = get_or_create_user(message.from_user.id)
    text = (
        f"Привет, {message.from_user.first_name or 'игрок'}!\n\n"
        f"У тебя сейчас:\n"
        f"• Монеты: {user.get('coins', 0)}\n"
        f"• RP: {user.get('rp', 0)}\n"
        f"• Уровень BP: {user.get('bp_level', 0)}\n"
    )

    await message.answer(text + "\n" + WELCOME_TEXT, reply_markup=MAIN_KEYBOARD)


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = get_or_create_user(message.from_user.id)
    loot = user.get("lootboxes", {})
    text = (
        "🎮 *Твой профиль*\n\n"
        f"Монеты: *{user.get('coins', 0)}*\n"
        f"RP: *{user.get('rp', 0)}*\n"
        f"Уровень BP: *{user.get('bp_level', 0)}*\n\n"
        "🎁 Лутбоксы:\n"
        f"- Common: {loot.get('common', 0)}\n"
        f"- Uncommon: {loot.get('uncommon', 0)}\n"
        f"- Rare: {loot.get('rare', 0)}\n"
        f"- Epic: {loot.get('epic', 0)}\n"
        f"- Legendary: {loot.get('legendary', 0)}\n"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    """
    Простейшая заглушка для дейликов:
    - проверяем, выдавали ли уже сегодня;
    - если нет — просто показываем пример задач.
    Потом сюда можно прикрутить полноценную систему из твоего плана.
    """
    today = date.today().isoformat()

    def updater(user: dict):
        last = user.get("last_daily")
        # если ещё не выдавали дейлики сегодня — обновляем дату
        if last != today:
            user["last_daily"] = today
            # здесь можно начислять лутбокс/монеты за вход и т.п.

    update_user(message.from_user.id, updater)

    text = (
        "📅 *Пример ежедневных задач* (пока заглушка)\n\n"
        "Маленькие:\n"
        + "\n".join(f"• {t}" for t in SMALL_TASKS[:3])
        + "\n\nСредние:\n"
        + "\n".join(f"• {t}" for t in MEDIUM_TASKS[:3])
        + "\n\nБольшие:\n"
        + "\n".join(f"• {t}" for t in BIG_TASKS[:2])
        + "\n\nПозже сюда добавим полный список из твоего документа и систему монет."
    )

    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("lootbox"))
async def cmd_lootbox(message: Message):
    """
    Заглушка открытия лутбокса.
    Сейчас просто добавляет +1 common и +1 монету,
    чтобы было видно, что сохранение работает.
    """
    def updater(user: dict):
        user["coins"] = user.get("coins", 0) + 1
        loot = user.setdefault("lootboxes", {})
        loot["common"] = loot.get("common", 0) + 1

    update_user(message.from_user.id, updater)

    await message.answer(
        "🎁 Ты *условно* открыл Common-лутбокс.\n"
        "Пока без настоящего рандома и таблиц наград — это заглушка, "
        "но монетки и количество лутбоксов уже сохраняются в users.json.",
        parse_mode="Markdown",
    )


@dp.message(Command("map"))
async def cmd_map(message: Message):
    """
    Заглушка для карты прогресса (уровни 0–4 из твоего плана).
    Потом можно связать с реальными задачами и датами.
    """
    text = (
        "🗺 *Карта прогресса* (пока только текстовая заглушка)\n\n"
        "Уровень 0 — Старт\n"
        "Уровень 1 — Начало движения\n"
        "Уровень 2 — Разгоняемся\n"
        "Уровень 3 — Поддерживаем ритм\n"
        "Уровень 4 — Ускорение\n\n"
        "Дальше мы сможем подставлять сюда реальные цели и статусы."
    )
    await message.answer(text, parse_mode="Markdown")


# ---------
# КНОПКИ
# ---------

@dp.message(F.text == "🎮 Профиль")
async def btn_profile(message: Message):
    await cmd_profile(message)


@dp.message(F.text == "📅 Дейлики")
async def btn_daily(message: Message):
    await cmd_daily(message)


@dp.message(F.text == "🎁 Лутбоксы")
async def btn_lootbox(message: Message):
    await cmd_lootbox(message)


@dp.message(F.text == "🗺 Карта")
async def btn_map(message: Message):
    await cmd_map(message)


# ------------------------
# ЗАПУСК БОТА
# ------------------------

async def main():
    load_dotenv()  # .env для локальной разработки

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не установлена. "
            "Задай её в настройках хостинга или в .env файле."
        )

    bot = Bot(token=token)
    logger.info("Бот запускается (long polling)…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
