import os
import asyncio
import logging
import random
from datetime import datetime, date
from typing import List, Tuple, Optional
from urllib.parse import urlparse

import pg8000
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Defaults,
)

# ================== ЛОГИ ==================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")

# Railway обычно даёт DATABASE_URL вида:
# postgres://user:password@host:port/dbname
DATABASE_URL = os.getenv("DATABASE_URL")

# Если хочешь сделать бота приватным — впиши сюда свой Telegram ID
ALLOWED_USER_IDS = set()  # например {123456789}


# ================== ПОДКЛЮЧЕНИЕ К БД ==================


def _parse_db_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
    }


def _get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан в переменных окружения")
    cfg = _parse_db_url(DATABASE_URL)
    return pg8000.connect(**cfg)


def _init_db_sync():
    """Создаём таблицы, если их ещё нет, и добавляем недостающие колонки (ALTER)."""
    conn = _get_conn()
    cur = conn.cursor()

    # users
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            user_id    BIGINT PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            coins      INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    # на случай старой схемы без этих колонок:
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
    except Exception:
        pass
    try:
        cur.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS coins INTEGER DEFAULT 0"
        )
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TEXT")
    except Exception:
        pass

    # rewards
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rewards(
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT,
            name       TEXT,
            box_level  INTEGER,
            used       INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )

    # main_progress
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS main_progress(
            user_id    BIGINT,
            node_index INTEGER,
            status     TEXT,
            PRIMARY KEY(user_id, node_index)
        )
        """
    )

    # daily_tasks
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_tasks(
            user_id   BIGINT,
            task_code TEXT,
            day       TEXT,
            done      INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, task_code, day)
        )
        """
    )

    conn.commit()
    conn.close()
    logger.info("Схема БД инициализирована.")


async def init_db():
    await asyncio.to_thread(_init_db_sync)


# ================== DB-ОБЁРТКИ (портируем старую sqlite-логику) ==================

def _get_or_create_user_sync(
    user_id: int, username: str, first_name: str
) -> Tuple[int, str, str, int]:
    """
    Возвращает запись пользователя (user_id, username, first_name, coins).
    Если пользователя нет — создаёт с 50 монетами.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username, first_name, coins FROM users WHERE user_id=%s",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None:
        coins = 50
        cur.execute(
            """
            INSERT INTO users(user_id, username, first_name, coins, created_at)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (user_id, username, first_name, coins, datetime.utcnow().isoformat()),
        )
        conn.commit()
        row = (user_id, username, first_name, coins)
    else:
        # Обновляем username/first_name на случай, если поменялись
        cur.execute(
            """
            UPDATE users
               SET username=%s,
                   first_name=%s
             WHERE user_id=%s
            """,
            (username, first_name, user_id),
        )
        conn.commit()
        row = (row[0], username, first_name, row[3])
    conn.close()
    return row


async def get_or_create_user(
    user_id: int, username: str, first_name: str
) -> Tuple[int, str, str, int]:
    return await asyncio.to_thread(
        _get_or_create_user_sync, user_id, username, first_name
    )


def _update_coins_sync(user_id: int, delta: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users(user_id, coins, created_at)
        VALUES (%s,%s,%s)
        ON CONFLICT (user_id) DO UPDATE
            SET coins = users.coins + EXCLUDED.coins
        """,
        (user_id, delta, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


async def update_coins(user_id: int, delta: int):
    await asyncio.to_thread(_update_coins_sync, user_id, delta)


def _get_coins_sync(user_id: int) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT coins FROM users WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


async def get_coins(user_id: int) -> int:
    return await asyncio.to_thread(_get_coins_sync, user_id)


def _add_reward_sync(user_id: int, name: str, box_level: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rewards(user_id, name, box_level, used, created_at)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (user_id, name, box_level, 0, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


async def add_reward(user_id: int, name: str, box_level: int):
    await asyncio.to_thread(_add_reward_sync, user_id, name, box_level)


def _get_active_rewards_sync(user_id: int) -> List[Tuple[int, str, int]]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, box_level
          FROM rewards
         WHERE user_id=%s AND used=0
         ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


async def get_active_rewards(user_id: int) -> List[Tuple[int, str, int]]:
    return await asyncio.to_thread(_get_active_rewards_sync, user_id)


def _mark_reward_used_sync(reward_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE rewards SET used=1 WHERE id=%s", (reward_id,))
    conn.commit()
    conn.close()


async def mark_reward_used(reward_id: int):
    await asyncio.to_thread(_mark_reward_used_sync, reward_id)


def _get_main_status_sync(user_id: int, node_index: int) -> str:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT status FROM main_progress
         WHERE user_id=%s AND node_index=%s
        """,
        (user_id, node_index),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "locked"


async def get_main_status(user_id: int, node_index: int) -> str:
    return await asyncio.to_thread(_get_main_status_sync, user_id, node_index)


def _set_main_status_sync(user_id: int, node_index: int, status: str):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO main_progress(user_id, node_index, status)
        VALUES (%s,%s,%s)
        ON CONFLICT (user_id, node_index) DO UPDATE
            SET status = EXCLUDED.status
        """,
        (user_id, node_index, status),
    )
    conn.commit()
    conn.close()


async def set_main_status(user_id: int, node_index: int, status: str):
    await asyncio.to_thread(_set_main_status_sync, user_id, node_index, status)


def _get_daily_done_sync(user_id: int, task_code: str, day: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT done FROM daily_tasks
         WHERE user_id=%s AND task_code=%s AND day=%s
        """,
        (user_id, task_code, day),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row[0]) if row else False


async def get_daily_done(user_id: int, task_code: str, day: str) -> bool:
    return await asyncio.to_thread(_get_daily_done_sync, user_id, task_code, day)


def _set_daily_done_sync(user_id: int, task_code: str, day: str, done: bool):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO daily_tasks(user_id, task_code, day, done)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (user_id, task_code, day) DO UPDATE
            SET done = EXCLUDED.done
        """,
        (user_id, task_code, day, 1 if done else 0),
    )
    conn.commit()
    conn.close()


async def set_daily_done(user_id: int, task_code: str, day: str, done: bool):
    await asyncio.to_thread(_set_daily_done_sync, user_id, task_code, day, done)


# ================== ИГРОВАЯ КОНФИГА (как в старом коде) ==================

LOOTBOXES = {
    1: {"name": "Little Happiness", "price": 10},
    2: {"name": "Medium Loot Box", "price": 20},
    3: {"name": "Large Loot Box", "price": 40},
    4: {"name": "Epic Loot Box", "price": 80},
    5: {"name": "Legendary Loot Box", "price": 150},
}

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

REWARD_CARDS = {
    "common": {"label": "🟦 Обычная карта награды"},
    "uncommon": {"label": "🟩 Необычная карта награды"},
    "rare": {"label": "🟪 Редкая карта награды"},
    "epic": {"label": "🟧 Эпическая карта награды"},
    "legendary": {"label": "🟥 Легендарная карта награды"},
}

MAIN_QUESTS = [
    {
        "index": 1,
        "title": "Инвентаризация денег и долгов",
        "desc": (
            "1) Выписать ВСЕ долги и обязательства: ипотека, 500$ за подъем материалов, "
            "штраф 100 лари, 70 000₽ рассрочка и т.д.\n"
            "2) Отдельно выписать ежемесячные расходы: коммуналка, интернет, телефон, собака.\n"
            "3) Подсчитать, сколько нужно в месяц, чтобы жить без паники."
        ),
        "reward_coins": 20,
        "reward_card": "uncommon",
    },
    {
        "index": 2,
        "title": "План закрытия долгов до лета",
        "desc": (
            "1) Разбить крупные долги на месячные шаги до лета.\n"
            "2) Решить, с чего начинаешь (что критичнее).\n"
            "3) Составить черновой график: какие суммы в какие месяцы гасишь."
        ),
        "reward_coins": 25,
        "reward_card": "uncommon",
    },
    {
        "index": 3,
        "title": "Разогрев апворка",
        "desc": (
            "1) Обновить портфолио и профиль под текущий фокус.\n"
            "2) Подготовить 2–3 шаблона откликов под разные типы заказов.\n"
            "3) Сделать минимум 5 осознанных откликов за неделю."
        ),
        "reward_coins": 30,
        "reward_card": "rare",
    },
    {
        "index": 4,
        "title": "Первая «рабочая неделя апворка»",
        "desc": (
            "1) 5 рабочих дней с хотя бы одним фокус-слотом апворка.\n"
            "2) Вести учёт: сколько часов и сколько заработала.\n"
            "3) Подвести итоги в конце недели (что сработало / что нет)."
        ),
        "reward_coins": 40,
        "reward_card": "rare",
    },
    {
        "index": 5,
        "title": "План ремонта квартиры под сдачу",
        "desc": (
            "1) Разбить квартиру на зоны: ванная, кухня, спальни, коридор, балконы.\n"
            "2) Для каждой зоны решить уровень ремонта: «просто, но красиво».\n"
            "3) Оценить примерный бюджет по зонам + приоритеты (что в первую очередь)."
        ),
        "reward_coins": 50,
        "reward_card": "epic",
    },
    {
        "index": 6,
        "title": "Финансовый план: ремонт + жизнь 3/3",
        "desc": (
            "1) Посчитать, сколько нужно накопить к маю на ремонт.\n"
            "2) Посчитать бюджет жизни 3/3: Батуми ↔ Тбилиси (аренда, метро, еда).\n"
            "3) Разбить всё это на месячные цели по накоплениям."
        ),
        "reward_coins": 60,
        "reward_card": "epic",
    },
    {
        "index": 7,
        "title": "Тест-поездка: жизнь 3/3 с Тбилиси",
        "desc": (
            "1) Выбрать район и примерную квартиру под тестовый заезд в Тбилиси.\n"
            "2) Составить план: сколько там живёте, сколько в Батуми.\n"
            "3) Сделать первый пробный заезд (даже короткий) и записать ощущения."
        ),
        "reward_coins": 80,
        "reward_card": "legendary",
    },
]

DAILY_TASKS = {
    "work_1": {
        "title": "1 фокус-слот работы (25–50 мин)",
        "coins": 4,
    },
    "work_2": {
        "title": "Ответить на важные сообщения/клиентов",
        "coins": 3,
    },
    "self_1": {
        "title": "Мини-уход за собой (душ/крем/что-то милое)",
        "coins": 2,
    },
    "home_1": {
        "title": "10 минут уборки или разбора завалов",
        "coins": 2,
    },
    "rest_1": {
        "title": "Осознанный отдых 15 минут без телефона",
        "coins": 2,
    },
}


def roll_reward(box_level: int) -> str:
    roll = random.randint(1, 100)
    for threshold, name in REWARD_TABLE[box_level]:
        if roll <= threshold:
            return f"{name} (d100={roll})"
    return f"Сюрприз (d100={roll})"


# ================== TELEGRAM UI ==================

def main_menu_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📍 Квест-карта", callback_data="menu:map")],
        [InlineKeyboardButton(text="📝 Дейлики", callback_data="menu:dailies")],
        [InlineKeyboardButton(text="🎁 Лутбоксы", callback_data="menu:loot")],
        [InlineKeyboardButton(text="📦 Инвентарь", callback_data="menu:inv")],
        [InlineKeyboardButton(text="💰 Профиль", callback_data="menu:profile")],
    ]
    return InlineKeyboardMarkup(kb)


def access_denied(user_id: int) -> bool:
    return ALLOWED_USER_IDS and (user_id not in ALLOWED_USER_IDS)


# ---------- АНИМАЦИИ ----------

async def show_path_animation(message, quest_title: str):
    frames = [
        "🗺 Ты смотришь на карту…",
        "🗺✨ Жёлтая дорожка начинает подсвечиваться.",
        f"🔻 Фишка перемещается к узлу: <b>{quest_title}</b>.",
        "✨ Ветка слегка мерцает — квест доступен.",
    ]
    msg = await message.reply_text(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        await msg.edit_text(frame)
    await asyncio.sleep(0.4)


async def show_card_animation(message, card_label: str):
    frames = [
        "🃏 Ты достаёшь карту-награду…",
        "🃏✨ На рубашке проступают золотые узоры.",
        f"🃏💫 Карта раскрывается: <b>{card_label}</b>!",
    ]
    msg = await message.reply_text(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        await msg.edit_text(frame)
    await asyncio.sleep(0.4)


# ---------- /start и /menu ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if access_denied(user.id):
        await update.message.reply_text("Этот бот приватный 🌙")
        return

    # создаём/обновляем пользователя
    await get_or_create_user(
        user.id, user.username or "", user.first_name or user.full_name or ""
    )

    # разлочим первый квест
    if await get_main_status(user.id, 1) == "locked":
        await set_main_status(user.id, 1, "active")

    coins = await get_coins(user.id)

    text = (
        "🌈 <b>Твоя дофаминовая игра запущена!</b>\n\n"
        "• Делай реальные квесты и дейлики\n"
        "• Получай монеты\n"
        "• Открывай лутбоксы и копи карты-награды\n\n"
        f"Сейчас у тебя <b>{coins}</b> монет.\n\n"
        "Открыть главное меню: /menu"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if access_denied(user.id):
        await update.message.reply_text("Этот бот приватный 🌙")
        return

    coins = await get_coins(user.id)
    await update.message.reply_text(
        f"🏠 <b>Главное меню</b>\nМонет: <b>{coins}</b>", reply_markup=main_menu_kb()
    )


# ---------- Обработка разделов меню ----------

async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    await callback.answer()
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    section = callback.data.split(":", 1)[1]

    # КВЕСТ-КАРТА
    if section == "map":
        lines = ["📍 <b>Квест-карта</b>\n"]
        for q in MAIN_QUESTS:
            status = await get_main_status(uid, q["index"])
            if status == "done":
                mark = "✅"
            elif status == "active":
                mark = "🟡"
            else:
                mark = "🔒"
            lines.append(f"{mark} {q['index']}. {q['title']}")

        active_index: Optional[int] = None
        for q in MAIN_QUESTS:
            if await get_main_status(uid, q["index"]) == "active":
                active_index = q["index"]
                break

        kb = []
        if active_index is not None:
            kb.append(
                [
                    InlineKeyboardButton(
                        text="📖 Открыть активный квест",
                        callback_data=f"quest:{active_index}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb)
        )

    # ДЕЙЛИКИ
    elif section == "dailies":
        today = date.today().isoformat()
        lines = ["📝 <b>Дейлики на сегодня</b>\n"]
        kb = []

        for code, info in DAILY_TASKS.items():
            done = await get_daily_done(uid, code, today)
            mark = "✅" if done else "⬜"
            lines.append(f"{mark} {info['title']} (+{info['coins']} монет)")
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:14]}…",
                        callback_data=f"daily:{code}",
                    )
                ]
            )

        kb.append([InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb)
        )

    # ЛУТБОКСЫ
    elif section == "loot":
        coins = await get_coins(uid)
        text = "🎁 <b>Лутбоксы</b>\n\n"
        for lvl, box in LOOTBOXES.items():
            text += f"{lvl}. {box['name']} — <b>{box['price']}</b> монет\n"
        text += (
            f"\nУ тебя сейчас <b>{coins}</b> монет.\n"
            "Выбери лутбокс, чтобы купить и открыть."
        )

        kb = []
        for lvl, box in LOOTBOXES.items():
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{lvl}. {box['name']}", callback_data=f"buy:{lvl}"
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")])

        await callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(kb)
        )

    # ИНВЕНТАРЬ
    elif section == "inv":
        rewards = await get_active_rewards(uid)
        if not rewards:
            text = (
                "📦 Твой инвентарь пока пуст.\n\n"
                "Заработай монеты за квесты или дейлики и открой лутбокс 🎁\n"
                "Или получи карту-награду за Мейн-квест."
            )
            kb = [
                [InlineKeyboardButton("🎁 К лутбоксам", callback_data="menu:loot")],
                [InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")],
            ]
        else:
            lines = ["📦 <b>Инвентарь</b>\n"]
            kb = []
            for rid, name, lvl in rewards:
                prefix = "🃏" if lvl == 0 else f"[L{lvl}]"
                lines.append(f"• {prefix} {name}")
                kb.append(
                    [
                        InlineKeyboardButton(
                            text=f"Использовать: {name[:18]}…",
                            callback_data=f"use:{rid}",
                        )
                    ]
            )
            kb.append([InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")])
            text = "\n".join(lines)

        await callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(kb)
        )

    # ПРОФИЛЬ
    elif section in ("profile", "root"):
        coins = await get_coins(uid)
        text = f"🏠 <b>Главное меню</b>\nМонет: <b>{coins}</b>"
        await callback.message.edit_text(text, reply_markup=main_menu_kb())


# ---------- КВЕСТЫ ----------

async def cb_open_quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    idx = int(callback.data.split(":", 1)[1])
    quest = next((q for q in MAIN_QUESTS if q["index"] == idx), None)
    if quest is None:
        await callback.answer("Квест не найден", show_alert=True)
        return

    status = await get_main_status(uid, idx)
    if status == "locked":
        await callback.answer("Этот квест ещё закрыт 🔒", show_alert=True)
        return

    await show_path_animation(callback.message, quest["title"])

    text = (
        f"📖 <b>Квест {idx}: {quest['title']}</b>\n\n"
        f"{quest['desc']}\n\n"
        f"Награда: <b>{quest['reward_coins']}</b> монет и карта-награда "
        f"{REWARD_CARDS[quest['reward_card']]['label']}."
    )
    kb = [
        [
            InlineKeyboardButton(
                "✅ Я это сделала", callback_data=f"quest_done:{idx}"
            )
        ],
        [InlineKeyboardButton("⬅ Назад к карте", callback_data="menu:map")],
    ]
    await callback.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(kb)
    )
    await callback.answer()


async def cb_quest_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    idx = int(callback.data.split(":", 1)[1])
    quest = next((q for q in MAIN_QUESTS if q["index"] == idx), None)
    if quest is None:
        await callback.answer("Квест не найден", show_alert=True)
        return

    status = await get_main_status(uid, idx)
    if status == "done":
        await callback.answer("Этот квест уже закрыт ✅", show_alert=True)
        return

    await set_main_status(uid, idx, "done")

    next_q = next((q for q in MAIN_QUESTS if q["index"] == idx + 1), None)
    if next_q and (await get_main_status(uid, next_q["index"])) == "locked":
        await set_main_status(uid, next_q["index"], "active")

    coins_reward = quest["reward_coins"]
    await update_coins(uid, coins_reward)

    card_key = quest["reward_card"]
    card_cfg = REWARD_CARDS.get(card_key, REWARD_CARDS["common"])
    card_name = card_cfg["label"] + f" (за квест {idx})"

    await add_reward(uid, card_name, 0)

    await show_card_animation(callback.message, card_cfg["label"])

    text = (
        f"🎉 <b>Квест {idx} выполнен!</b>\n\n"
        f"Ты получила <b>{coins_reward}</b> монет и карту-награду:\n"
        f"{card_cfg['label']}\n\n"
        "Карта добавлена в инвентарь. Когда захочешь, можешь «обналичить» её "
        "в реальном мире (выбрать приз из этого диапазона).\n\n"
        "Открыть меню: /menu"
    )
    await callback.message.reply_text(text)
    await callback.answer()


# ---------- ДЕЙЛИКИ ----------

async def cb_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    code = callback.data.split(":", 1)[1]
    if code not in DAILY_TASKS:
        await callback.answer("Нет такого задания", show_alert=True)
        return

    today = date.today().isoformat()
    done_before = await get_daily_done(uid, code, today)

    if not done_before:
        await set_daily_done(uid, code, today, True)
        coins = DAILY_TASKS[code]["coins"]
        await update_coins(uid, coins)
        await callback.answer(f"+{coins} монет 💰", show_alert=False)
    else:
        await set_daily_done(uid, code, today, False)
        coins = DAILY_TASKS[code]["coins"]
        await update_coins(uid, -coins)
        await callback.answer(f"-{coins} монет (отмена задания)", show_alert=False)

    # перерисуем список дейликов
    today = date.today().isoformat()
    lines = ["📝 <b>Дейлики на сегодня</b>\n"]
    kb = []
    for c, info in DAILY_TASKS.items():
        done = await get_daily_done(uid, c, today)
        mark = "✅" if done else "⬜"
        lines.append(f"{mark} {info['title']} (+{info['coins']} монет)")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:14]}…",
                    callback_data=f"daily:{c}",
                )
            ]
        )
    kb.append([InlineKeyboardButton("⬅ В меню", callback_data="menu:profile")])

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb)
    )


# ---------- ЛУТБОКСЫ ----------

async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    lvl = int(callback.data.split(":", 1)[1])
    box = LOOTBOXES.get(lvl)
    if not box:
        await callback.answer("Нет такого лутбокса", show_alert=True)
        return

    coins = await get_coins(uid)
    if coins < box["price"]:
        await callback.answer("Недостаточно монет 💸", show_alert=True)
        return

    await update_coins(uid, -box["price"])

    msg = await callback.message.reply_text("🎁 Лутбокс куплен. Открываем…")
    await asyncio.sleep(0.5)
    await msg.edit_text("🎁✨ Внутри что-то шуршит…")
    await asyncio.sleep(0.5)
    await msg.edit_text("🎁✨💥 Яркая вспышка…")
    await asyncio.sleep(0.6)

    reward_name = roll_reward(lvl)
    await add_reward(uid, reward_name, lvl)

    await msg.edit_text(
        f"🌟 <b>{box['name']} открыт!</b>\n\n"
        f"Тебе выпало:\n<b>{reward_name}</b>\n\n"
        "Награда добавлена в инвентарь. /menu"
    )
    await callback.answer()


# ---------- ИСПОЛЬЗОВАНИЕ НАГРАД ----------

async def cb_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    uid = callback.from_user.id

    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    rid = int(callback.data.split(":", 1)[1])
    await mark_reward_used(rid)

    await callback.answer("Награда использована ✨", show_alert=False)
    await callback.message.reply_text(
        "✅ Награда помечена как использованная.\n"
        "Теперь можно реализовать её в реальности 💛"
    )


# ================== ЗАПУСК ==================

async def run_bot():
    logger.info("Инициализирую БД…")
    await init_db()

    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = (
        Application.builder().token(BOT_TOKEN).defaults(defaults).build()
    )

    # команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))

    # callback-и
    application.add_handler(CallbackQueryHandler(cb_menu, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(cb_open_quest, pattern=r"^quest:"))
    application.add_handler(
        CallbackQueryHandler(cb_quest_done, pattern=r"^quest_done:")
    )
    application.add_handler(CallbackQueryHandler(cb_daily, pattern=r"^daily:"))
    application.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    application.add_handler(CallbackQueryHandler(cb_use, pattern=r"^use:"))

    logger.info("Запуск бота (long polling)…")
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(run_bot())
