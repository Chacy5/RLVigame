import asyncio
import json
import logging
import os
import random
import sqlite3
from datetime import datetime, date
from typing import List, Tuple, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ================== ЛОГИ ==================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================== НАСТРОЙКИ ==================

DB_PATH = "game_bot.db"

# 1) Пытаемся прочитать токен из users.json
def load_token_from_file() -> Optional[str]:
    try:
        with open("users.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        # подстрой под свою структуру файла, если нужно
        return data.get("token") or data.get("BOT_TOKEN")
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Не удалось прочитать users.json: {e}")
        return None


BOT_TOKEN = load_token_from_file() or os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
    # Не падаем с ошибкой, но явно пишем в логи
    logger.error("BOT_TOKEN не задан или задан как PASTE_YOUR_TOKEN_HERE. "
                 "Укажи реальный токен в users.json или переменной окружения BOT_TOKEN!")

# Если хочешь сделать бота приватным — впиши сюда свой Telegram ID
# Узнать можно у @userinfobot
ALLOWED_USER_IDS = set()  # напр. {123456789}


# ================== БАЗА ДАННЫХ ==================

def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    logger.info("Инициализирую БД…")
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id    INTEGER PRIMARY KEY,
        coins      INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS rewards(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        name       TEXT,
        box_level  INTEGER,
        used       INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS main_progress(
        user_id    INTEGER,
        node_index INTEGER,
        status     TEXT,
        PRIMARY KEY(user_id, node_index)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS daily_tasks(
        user_id   INTEGER,
        task_code TEXT,
        day       TEXT,
        done      INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, task_code, day)
    )
    """)

    conn.commit()
    conn.close()
    logger.info("Схема БД инициализирована.")


def get_or_create_user(user_id: int) -> int:
    """
    Возвращает текущий баланс монет.
    Если пользователя нет — создаёт с 50 монетами.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO users(user_id, coins, created_at) VALUES(?,?,?)",
            (user_id, 50, datetime.utcnow().isoformat()),
        )
        conn.commit()
        coins = 50
    else:
        coins = row[0]
    conn.close()
    return coins


def update_coins(user_id: int, delta: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO users(user_id, coins, created_at)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET coins = coins + ?
    """, (user_id, 0, datetime.utcnow().isoformat(), delta))
    conn.commit()
    conn.close()


def get_coins(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def add_reward(user_id: int, name: str, box_level: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO rewards(user_id, name, box_level, used, created_at) "
        "VALUES(?,?,?,?,?)",
        (user_id, name, box_level, 0, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_active_rewards(user_id: int) -> List[Tuple[int, str, int]]:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, box_level FROM rewards "
        "WHERE user_id = ? AND used = 0 ORDER BY id DESC",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def mark_reward_used(reward_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE rewards SET used = 1 WHERE id = ?", (reward_id,))
    conn.commit()
    conn.close()


def get_main_status(user_id: int, node_index: int) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT status FROM main_progress WHERE user_id = ? AND node_index = ?",
        (user_id, node_index),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else "locked"


def set_main_status(user_id: int, node_index: int, status: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO main_progress(user_id, node_index, status)
        VALUES(?,?,?)
        ON CONFLICT(user_id, node_index) DO UPDATE SET status = ?
    """, (user_id, node_index, status, status))
    conn.commit()
    conn.close()


def get_daily_done(user_id: int, task_code: str, day: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT done FROM daily_tasks
        WHERE user_id = ? AND task_code = ? AND day = ?
    """, (user_id, task_code, day))
    row = c.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def set_daily_done(user_id: int, task_code: str, day: str, done: bool):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO daily_tasks(user_id, task_code, day, done)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id, task_code, day) DO UPDATE SET done = ?
    """, (user_id, task_code, day, 1 if done else 0, 1 if done else 0))
    conn.commit()
    conn.close()


# ================== ИГРОВАЯ КОНФИГА ==================
# ВАЖНО: здесь ты потом просто подставишь данные из своего DOC/XLSX
# Сейчас стоят примеры, чтобы бот уже работал.

LOOTBOXES = {
    1: {"name": "Little Happiness",   "price": 10},
    2: {"name": "Medium Loot Box",    "price": 20},
    3: {"name": "Large Loot Box",     "price": 40},
    4: {"name": "Epic Loot Box",      "price": 80},
    5: {"name": "Legendary Loot Box", "price": 150},
}

# Упрощённые d100-таблицы для лутбоксов.
# Из твоей lootbox.xlsx мы сможем потом просто переписать сюда значения.
REWARD_TABLE = {
    1: [
        (40, "🧁 Маленькая вкусняшка"),
        (70, "☕ Маленький кофе"),
        (90, "🎀 Милый стикер/мелочь"),
        (100, "🌟 Мини-набор радости (3 маленьких предмета)"),
    ],
    2: [
        (40, "🍫 Набор сладостей"),
        (70, "🎮 Небольшой игровой бонус/скин"),
        (90, "🔌 Мини-аксессуар"),
        (100, "🌟 MTG + мини-техника"),
    ],
    3: [
        (40, "🎮 Игра на скидке"),
        (70, "📦 Полезный гаджет"),
        (90, "🃏 MTG мини-набор"),
        (100, "🌟 Крупная награда + вкусняшки"),
    ],
    4: [
        (40, "🃏 MTG эпический набор"),
        (70, "🔊 Хорошая колонка/техника"),
        (90, "🎮 Крупная игра/DLC"),
        (100, "🌟 Крупная покупка + бонус"),
    ],
    5: [
        (40, "🃏 MTG премиальный продукт"),
        (70, "🖥️ Крупная техника"),
        (90, "🎮 Игра мечты"),
        (100, "💖 Техника + MTG + подарок от себя"),
    ],
}

# Карты-награды за Мейн-квесты
REWARD_CARDS = {
    "common":    {"label": "🟦 Обычная карта награды"},
    "uncommon":  {"label": "🟩 Необычная карта награды"},
    "rare":      {"label": "🟪 Редкая карта награды"},
    "epic":      {"label": "🟧 Эпическая карта награды"},
    "legendary": {"label": "🟥 Легендарная карта награды"},
}

# Здесь пока примерный набор мейн-квестов.
# Позже просто заменим на структуру из твоего документа.
MAIN_QUESTS = [
    {
        "index": 1,
        "title": "Инвентаризация денег и долгов",
        "desc": (
            "1) Выписать ВСЕ долги и обязательства.\n"
            "2) Выписать ежемесячные обязательные траты.\n"
            "3) Посчитать, сколько нужно в месяц, чтобы жить без паники."
        ),
        "reward_coins": 20,
        "reward_card": "uncommon",
    },
    {
        "index": 2,
        "title": "План закрытия долгов до лета",
        "desc": (
            "1) Разбить крупные долги на месячные шаги.\n"
            "2) Определить приоритеты.\n"
            "3) Составить черновой график погашения."
        ),
        "reward_coins": 25,
        "reward_card": "uncommon",
    },
    {
        "index": 3,
        "title": "Разогрев апворка",
        "desc": (
            "1) Обновить портфолио.\n"
            "2) Подготовить 2–3 шаблона откликов.\n"
            "3) Сделать минимум 5 осознанных откликов за неделю."
        ),
        "reward_coins": 30,
        "reward_card": "rare",
    },
]

# Дейлики (примеры; потом заменим на твой список)
DAILY_TASKS = {
    "work_1": {
        "title": "1 фокус-слот работы (25–50 мин)",
        "coins": 4
    },
    "work_2": {
        "title": "Ответить на важные сообщения/клиентов",
        "coins": 3
    },
    "self_1": {
        "title": "Мини-уход за собой",
        "coins": 2
    },
    "home_1": {
        "title": "10 минут уборки/разбора завалов",
        "coins": 2
    },
    "rest_1": {
        "title": "Осознанный отдых 15 минут без телефона",
        "coins": 2
    },
}


def roll_reward(box_level: int) -> str:
    roll = random.randint(1, 100)
    for threshold, name in REWARD_TABLE[box_level]:
        if roll <= threshold:
            return f"{name} (d100={roll})"
    return f"Сюрприз (d100={roll})"


# ================== TELEGRAM-БОТ (aiogram 3) ==================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📍 Квест-карта", callback_data="menu:map")],
        [InlineKeyboardButton(text="📝 Дейлики", callback_data="menu:dailies")],
        [InlineKeyboardButton(text="🎁 Лутбоксы", callback_data="menu:loot")],
        [InlineKeyboardButton(text="📦 Инвентарь", callback_data="menu:inv")],
        [InlineKeyboardButton(text="💰 Профиль", callback_data="menu:profile")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def access_denied(user_id: int) -> bool:
    """True, если бот приватный и этот user_id не в списке."""
    return ALLOWED_USER_IDS and (user_id not in ALLOWED_USER_IDS)


# ---------- АНИМАЦИИ ----------

async def show_path_animation(message: Message, quest_title: str):
    frames = [
        "🗺 Ты смотришь на карту…",
        "🗺✨ Жёлтая дорожка начинает подсвечиваться.",
        f"🔻 Фишка перемещается к узлу: <b>{quest_title}</b>.",
        "✨ Ветка слегка мерцает — квест доступен.",
    ]
    msg = await message.answer(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        await msg.edit_text(frame)
    await asyncio.sleep(0.4)


async def show_card_animation(message: Message, card_label: str):
    frames = [
        "🃏 Ты достаёшь карту-награду…",
        "🃏✨ На рубашке проступают золотые узоры.",
        f"🃏💫 Карта раскрывается: <b>{card_label}</b>!",
    ]
    msg = await message.answer(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        await msg.edit_text(frame)
    await asyncio.sleep(0.4)


# ---------- /start и /menu ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if access_denied(message.from_user.id):
        await message.answer("Этот бот приватный 🌙")
        return

    coins = get_or_create_user(message.from_user.id)

    # Разлочим первый квест, если ещё не активен
    if get_main_status(message.from_user.id, 1) == "locked":
        set_main_status(message.from_user.id, 1, "active")

    text = (
        "🌈 <b>Твоя дофаминовая игра запущена!</b>\n\n"
        "• Делай реальные квесты и дейлики\n"
        "• Получай монеты\n"
        "• Открывай лутбоксы и копи карты-награды\n\n"
        f"Сейчас у тебя <b>{coins}</b> монет.\n\n"
        "Открыть главное меню: /menu"
    )
    await message.answer(text, reply_markup=main_menu_kb())


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if access_denied(message.from_user.id):
        await message.answer("Этот бот приватный 🌙")
        return

    coins = get_coins(message.from_user.id)
    await message.answer(
        f"🏠 <b>Главное меню</b>\nМонет: <b>{coins}</b>",
        reply_markup=main_menu_kb(),
    )


# ---------- Обработка разделов меню ----------

@dp.callback_query(F.data.startswith("menu:"))
async def cb_menu(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    section = callback.data.split(":", 1)[1]

    # КВЕСТ-КАРТА
    if section == "map":
        lines = ["📍 <b>Квест-карта</b>\n"]
        for q in MAIN_QUESTS:
            status = get_main_status(uid, q["index"])
            if status == "done":
                mark = "✅"
            elif status == "active":
                mark = "🟡"
            else:
                mark = "🔒"
            lines.append(f"{mark} {q['index']}. {q['title']}")

        active_index = None
        for q in MAIN_QUESTS:
            if get_main_status(uid, q["index"]) == "active":
                active_index = q["index"]
                break

        kb = []
        if active_index is not None:
            kb.append([
                InlineKeyboardButton(
                    text="📖 Открыть активный квест",
                    callback_data=f"quest:{active_index}",
                )
            ])
        kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )

    # ДЕЙЛИКИ
    elif section == "dailies":
        today = date.today().isoformat()
        lines = ["📝 <b>Дейлики на сегодня</b>\n"]
        kb = []

        for code, info in DAILY_TASKS.items():
            done = get_daily_done(uid, code, today)
            mark = "✅" if done else "⬜"
            lines.append(f"{mark} {info['title']} (+{info['coins']} монет)")
            kb.append([
                InlineKeyboardButton(
                    text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:14]}…",
                    callback_data=f"daily:{code}",
                )
            ])

        kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )

    # ЛУТБОКСЫ
    elif section == "loot":
        coins = get_coins(uid)
        text = "🎁 <b>Лутбоксы</b>\n\n"
        for lvl, box in LOOTBOXES.items():
            text += f"{lvl}. {box['name']} — <b>{box['price']}</b> монет\n"
        text += f"\nУ тебя сейчас <b>{coins}</b> монет.\nВыбери лутбокс, чтобы купить и открыть."

        kb = []
        for lvl, box in LOOTBOXES.items():
            kb.append([
                InlineKeyboardButton(
                    text=f"{lvl}. {box['name']}",
                    callback_data=f"buy:{lvl}",
                )
            ])
        kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )

    # ИНВЕНТАРЬ
    elif section == "inv":
        rewards = get_active_rewards(uid)
        if not rewards:
            text = (
                "📦 Твой инвентарь пока пуст.\n\n"
                "Заработай монеты за квесты или дейлики и открой лутбокс 🎁\n"
                "Или получи карту-награду за Мейн-квест."
            )
            kb = [
                [InlineKeyboardButton(text="🎁 К лутбоксам", callback_data="menu:loot")],
                [InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")],
            ]
        else:
            lines = ["📦 <b>Инвентарь</b>\n"]
            kb = []
            for rid, name, lvl in rewards:
                if lvl == 0:
                    prefix = "🃏"
                else:
                    prefix = f"[L{lvl}]"
                lines.append(f"• {prefix} {name}")
                kb.append([
                    InlineKeyboardButton(
                        text=f"Использовать: {name[:18]}…",
                        callback_data=f"use:{rid}",
                    )
                ])
            kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])
            text = "\n".join(lines)

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )

    # ПРОФИЛЬ / ГЛАВНОЕ МЕНЮ
    elif section in ("profile", "root"):
        coins = get_coins(uid)
        text = f"🏠 <b>Главное меню</b>\nМонет: <b>{coins}</b>"
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_kb(),
        )

    await callback.answer()


# ---------- КВЕСТЫ ----------

@dp.callback_query(F.data.startswith("quest:"))
async def cb_open_quest(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    idx = int(callback.data.split(":", 1)[1])
    quest = next((q for q in MAIN_QUESTS if q["index"] == idx), None)
    if quest is None:
        await callback.answer("Квест не найден", show_alert=True)
        return

    status = get_main_status(uid, idx)
    if status == "locked":
        await callback.answer("Этот квест ещё закрыт 🔒", show_alert=True)
        return

    # Анимация движения по карте
    await show_path_animation(callback.message, quest["title"])

    text = (
        f"📖 <b>Квест {idx}: {quest['title']}</b>\n\n"
        f"{quest['desc']}\n\n"
        f"Награда: <b>{quest['reward_coins']}</b> монет и карта-награда "
        f"{REWARD_CARDS[quest['reward_card']]['label']}."
    )
    kb = [
        [InlineKeyboardButton(text="✅ Я это сделала", callback_data=f"quest_done:{idx}")],
        [InlineKeyboardButton(text="⬅ Назад к карте", callback_data="menu:map")],
    ]
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("quest_done:"))
async def cb_quest_done(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    idx = int(callback.data.split(":", 1)[1])
    quest = next((q for q in MAIN_QUESTS if q["index"] == idx), None)
    if quest is None:
        await callback.answer("Квест не найден", show_alert=True)
        return

    status = get_main_status(uid, idx)
    if status == "done":
        await callback.answer("Этот квест уже закрыт ✅", show_alert=True)
        return

    # отмечаем выполненным
    set_main_status(uid, idx, "done")

    # разлочим следующий
    next_q = next((q for q in MAIN_QUESTS if q["index"] == idx + 1), None)
    if next_q and get_main_status(uid, next_q["index"]) == "locked":
        set_main_status(uid, next_q["index"], "active")

    # награда монетами
    coins_reward = quest["reward_coins"]
    update_coins(uid, coins_reward)

    # карта-награда
    card_key = quest["reward_card"]
    card_cfg = REWARD_CARDS.get(card_key, REWARD_CARDS["common"])
    card_name = card_cfg["label"] + f" (за квест {idx})"

    # box_level = 0, чтобы отличать от лутбоксовых наград
    add_reward(uid, card_name, 0)

    # анимация открытия карты
    await show_card_animation(callback.message, card_cfg["label"])

    text = (
        f"🎉 <b>Квест {idx} выполнен!</b>\n\n"
        f"Ты получила <b>{coins_reward}</b> монет и карту-награду:\n"
        f"{card_cfg['label']}\n\n"
        "Карта добавлена в инвентарь. Когда захочешь, можешь «обналичить» её "
        "в реальном мире (выбрать приз из этого диапазона).\n\n"
        "Открыть меню: /menu"
    )
    await callback.message.answer(text)
    await callback.answer()


# ---------- ДЕЙЛИКИ ----------

@dp.callback_query(F.data.startswith("daily:"))
async def cb_daily(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    code = callback.data.split(":", 1)[1]
    if code not in DAILY_TASKS:
        await callback.answer("Нет такого задания", show_alert=True)
        return

    today = date.today().isoformat()
    done_before = get_daily_done(uid, code, today)

    if not done_before:
        set_daily_done(uid, code, today, True)
        coins = DAILY_TASKS[code]["coins"]
        update_coins(uid, coins)
        await callback.answer(f"+{coins} монет 💰", show_alert=False)
    else:
        set_daily_done(uid, code, today, False)
        coins = DAILY_TASKS[code]["coins"]
        update_coins(uid, -coins)
        await callback.answer(f"-{coins} монет (отмена задания)", show_alert=False)

    # перерисуем список дейликов
    today = date.today().isoformat()
    lines = ["📝 <b>Дейлики на сегодня</b>\n"]
    kb = []
    for c, info in DAILY_TASKS.items():
        done = get_daily_done(uid, c, today)
        mark = "✅" if done else "⬜"
        lines.append(f"{mark} {info['title']} (+{info['coins']} монет)")
        kb.append([
            InlineKeyboardButton(
                text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:14]}…",
                callback_data=f"daily:{c}",
            )
        ])
    kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


# ---------- ЛУТБОКСЫ ----------

@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    lvl = int(callback.data.split(":", 1)[1])
    box = LOOTBOXES.get(lvl)
    if not box:
        await callback.answer("Нет такого лутбокса", show_alert=True)
        return

    coins = get_coins(uid)
    if coins < box["price"]:
        await callback.answer("Недостаточно монет 💸", show_alert=True)
        return

    # списываем монеты
    update_coins(uid, -box["price"])

    # анимация открытия
    msg = await callback.message.answer("🎁 Лутбокс куплен. Открываем…")
    await asyncio.sleep(0.5)
    await msg.edit_text("🎁✨ Внутри что-то шуршит…")
    await asyncio.sleep(0.5)
    await msg.edit_text("🎁✨💥 Яркая вспышка…")
    await asyncio.sleep(0.6)

    reward_name = roll_reward(lvl)
    add_reward(uid, reward_name, lvl)

    await msg.edit_text(
        f"🌟 <b>{box['name']} открыт!</b>\n\n"
        f"Тебе выпало:\n<b>{reward_name}</b>\n\n"
        "Награда добавлена в инвентарь. /menu"
    )
    await callback.answer()


# ---------- ИСПОЛЬЗОВАНИЕ НАГРАД ----------

@dp.callback_query(F.data.startswith("use:"))
async def cb_use(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    rid = int(callback.data.split(":", 1)[1])
    mark_reward_used(rid)

    await callback.answer("Награда использована ✨", show_alert=False)
    await callback.message.answer(
        "✅ Награда помечена как использованная.\n"
        "Теперь можно реализовать её в реальности 💛"
    )


# ================== ЗАПУСК ==================

async def main():
    init_db()
    logger.info("Запуск бота (long polling)…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
