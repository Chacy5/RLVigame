"""
LifeQuest Telegram Bot — Railway + PostgreSQL (pg8000) + Webhook

Файлы в репозитории:
- main.py (этот файл)
- Лутбоксы.xlsx  (листы:
    "1. Маленькое счастье",
    "2. Средний",
    "3. Большой",
    "4. Эпический",
    "5. Легендарный",
    "Мини-ивенты"
  )

Переменные окружения Railway:
- TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
- DATABASE_URL        — postgres://... от Railway PostgreSQL plugin
- WEBHOOK_URL         — публичный URL Railway (например, https://myapp.up.railway.app)
- PARTNER_USER_ID     — (опционально) числовой Telegram ID парня
- LOOTBOX_XLS_PATH    — (опционально) путь к Excel, по умолчанию 'Лутбоксы.xlsx'

requirements.txt:
- python-telegram-bot==20.7
- pg8000==1.31.2
- openpyxl==3.1.5
"""

import logging
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse

import pg8000
from openpyxl import load_workbook
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ========= НАСТРОЙКИ / ENV =========

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в переменных окружения.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Не задан DATABASE_URL (строка подключения к PostgreSQL).")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise RuntimeError("Не задан WEBHOOK_URL (публичный URL Railway).")

PARTNER_USER_ID_ENV = os.getenv("PARTNER_USER_ID")
PARTNER_USER_ID: Optional[int] = int(PARTNER_USER_ID_ENV) if PARTNER_USER_ID_ENV else None

LOOTBOX_XLS_PATH = os.getenv("LOOTBOX_XLS_PATH", "Лутбоксы.xlsx")

PORT = int(os.getenv("PORT", "8000"))  # Railway задаёт PORT, если нет — 8000

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ========= КОНСТАНТЫ И РЕДКОСТИ =========

RARITY_ORDER = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]

RARITY_TO_COINS = {
    "Common": 5,
    "Uncommon": 7,
    "Rare": 10,
    "Epic": 15,
    "Legendary": 20,
}


@dataclass
class MainQuest:
    id: str
    level: int
    title: str
    description: str
    rarity: str


# ========= MAIN QUESTS (уровни 0–7) =========

MAIN_QUESTS: Dict[str, MainQuest] = {
    # Level 0
    "0.1": MainQuest("0.1", 0, "Настроить игровую систему", "Подготовить бота/правила/структуру игры.", "Common"),
    "0.2": MainQuest("0.2", 0, "Подготовить стопки карточек", "Распечатать / вырезать все карты и лутбоксы.", "Common"),
    "0.3": MainQuest("0.3", 0, "Финансовый аудит", "Разобрать долги, счета, регулярные расходы.", "Common"),
    # Level 1
    "1.1": MainQuest("1.1", 1, "Оплатить штраф 100₾", "Закрыть штраф по коммуналке.", "Common"),
    "1.2": MainQuest("1.2", 1, "План погашения рассрочки", "Составить план закрытия 70 000₽ рассрочки.", "Common"),
    "1.3": MainQuest("1.3", 1, "Выплатить часть рассрочки", "Сделать первый ощутимый платёж по рассрочке.", "Uncommon"),
    "1.4": MainQuest("1.4", 1, "Обновить портфолио", "Обновить работы и описание на Upwork / др.", "Common"),
    "1.5": MainQuest("1.5", 1, "Первые 10 откликов", "Отправить 10 продуманных откликов.", "Common"),
    "1.6": MainQuest("1.6", 1, "Список работ по квартире", "Сделать список задач по ремонту.", "Common"),
    "1.7": MainQuest("1.7", 1, "Сбор референсов", "Насобирать визуальные референсы для ремонта.", "Uncommon"),
    "1.FINAL": MainQuest("1.FINAL", 1, "Финал 1", "Закончить все задачи уровня 1 до 12 декабря.", "Uncommon"),
    # Level 2
    "2.1": MainQuest("2.1", 2, "Закрыть рассрочку 70 000₽", "Полностью закрыть рассрочку.", "Rare"),
    "2.2": MainQuest("2.2", 2, "Часть долга 500$", "Оплатить часть долга 500$ за лифт.", "Rare"),
    "2.3": MainQuest("2.3", 2, "Первый заказ Upwork", "Получить первый заказ.", "Uncommon"),
    "2.4": MainQuest("2.4", 2, "Выполнить >100$", "Отработать и получить доход более 100$.", "Rare"),
    "2.5": MainQuest("2.5", 2, "Второй заказ Upwork", "Получить второй заказ.", "Uncommon"),
    "2.6": MainQuest("2.6", 2, "Накопить 500$", "Финансовая подушка 500$.", "Uncommon"),
    "2.7": MainQuest("2.7", 2, "Накопить 1000$", "Финансовая подушка 1000$.", "Rare"),
    "2.8": MainQuest("2.8", 2, "Получить сметы", "Получить сметы по ремонту.", "Common"),
    "2.FINAL": MainQuest("2.FINAL", 2, "Финал 2", "Закончить все задачи уровня 2 к 7 января.", "Rare"),
    # Level 3
    "3.1": MainQuest("3.1", 3, "Закрыть долг 500$", "Полностью закрыть долг 500$.", "Epic"),
    "3.2": MainQuest("3.2", 3, "Доход 1000$/мес", "Стабильный доход 1000$/мес с Upwork/работы.", "Rare"),
    "3.3": MainQuest("3.3", 3, "Доход 1500$/мес", "Стабильный доход 1500$/мес.", "Epic"),
    "3.4": MainQuest("3.4", 3, "Накопить 2000$", "Накопить 2000$ под ремонт / подушку.", "Rare"),
    "3.5": MainQuest("3.5", 3, "Накопить 3000$", "Накопить 3000$.", "Epic"),
    "3.6": MainQuest("3.6", 3, "Финальный список материалов", "Составить финальный список материалов для ремонта.", "Uncommon"),
    "3.7": MainQuest("3.7", 3, "Стиль/палитра", "Выбрать стиль и палитру для ремонта.", "Uncommon"),
    "3.FINAL": MainQuest("3.FINAL", 3, "Финал 3", "Закончить все задачи уровня 3 до 20 февраля.", "Epic"),
    # Level 4
    "4.1": MainQuest("4.1", 4, "Накопить 4000$", "Достигнуть суммы 4000$.", "Epic"),
    "4.2": MainQuest("4.2", 4, "Накопить 5000$", "Достигнуть суммы 5000$.", "Legendary"),
    "4.3": MainQuest("4.3", 4, "5 заказов подряд", "Сделать 5 заказов подряд без провалов.", "Rare"),
    "4.4": MainQuest("4.4", 4, "Суперпродуктивная неделя", "Неделя суперактивной работы.", "Rare"),
    "4.5": MainQuest("4.5", 4, "Купить материалы", "Закупить материалы для ремонта.", "Uncommon"),
    "4.6": MainQuest("4.6", 4, "Договор с мастерами", "Закрыть договор с мастерами.", "Rare"),
    "4.FINAL": MainQuest("4.FINAL", 4, "Финал 4", "Финал уровня 4 до 20 марта.", "Legendary"),
    # Level 5
    "5.1": MainQuest("5.1", 5, "Ванная", "Закончить ремонт ванной.", "Rare"),
    "5.2": MainQuest("5.2", 5, "Кухня", "Закончить ремонт кухни.", "Rare"),
    "5.3": MainQuest("5.3", 5, "Стены", "Закончить стены.", "Rare"),
    "5.4": MainQuest("5.4", 5, "Свет", "Освещение по всей квартире.", "Common"),
    "5.5": MainQuest("5.5", 5, "Балконы", "Сделать балконы.", "Uncommon"),
    "5.FINAL": MainQuest("5.FINAL", 5, "Финал 5", "Финал ремонтного марафона.", "Epic"),
    # Level 6
    "6.1": MainQuest("6.1", 6, "Уборка", "Финальная уборка перед сдачей.", "Common"),
    "6.2": MainQuest("6.2", 6, "Фото", "Сделать хорошие фотографии квартиры.", "Uncommon"),
    "6.3": MainQuest("6.3", 6, "Риэлтор", "Найти / заключить договор с риэлтором.", "Uncommon"),
    "6.4": MainQuest("6.4", 6, "Объявление", "Сделать объявление / разместить.", "Common"),
    "6.5": MainQuest("6.5", 6, "Первая бронь", "Получить первую бронь.", "Rare"),
    "6.6": MainQuest("6.6", 6, "Первый платёж", "Получить первый платёж от арендатора.", "Epic"),
    "6.FINAL": MainQuest("6.FINAL", 6, "Финал 6", "Финальный уровень про сдачу квартиры.", "Legendary"),
    # Level 7
    "7.1": MainQuest("7.1", 7, "1500$ на Тбилиси", "Накопить 1500$ на переезд/жильё в Тбилиси.", "Rare"),
    "7.2": MainQuest("7.2", 7, "2000$ финальная цель", "Накопить 2000$ (3 месяца + депозит).", "Epic"),
    "7.3": MainQuest("7.3", 7, "Найти квартиру", "Подобрать квартиру рядом с метро.", "Rare"),
    "7.4": MainQuest("7.4", 7, "Оплатить жильё 2–3 месяца", "Оплатить жильё вперёд на 2–3 месяца.", "Epic"),
    "7.5": MainQuest("7.5", 7, "Организовать переезд", "Логистика и переезд.", "Uncommon"),
    "7.6": MainQuest("7.6", 7, "Создать уют", "Настроить уют в новой квартире.", "Rare"),
    "7.FINAL": MainQuest("7.FINAL", 7, "Финал финалов", "Большой финал — 31 мая 2025.", "Legendary"),
}


# ========= ЛУТБОКСЫ И МИНИ-ИВЕНТЫ ИЗ EXCEL (openpyxl) =========

def _extract_rewards_ws(ws) -> List[str]:
    """
    ws — лист с таблицей:
    | № | Награда |

    Возвращаем список длиной 100, где индекс 0 — номер 1, индекс 99 — номер 100.
    """
    res: Dict[int, str] = {}
    first = True
    for row in ws.iter_rows(values_only=True):
        if first:
            first = False  # пропускаем заголовок
            continue
        if not row or row[0] is None:
            continue
        try:
            n = int(row[0])
        except (ValueError, TypeError):
            continue
        if n < 1 or n > 100:
            continue
        text = ""
        if len(row) > 1 and row[1] is not None:
            text = str(row[1]).strip()
        if not text or text.lower() == "nan":
            continue
        res[n] = text
    # заполняем пропуски плейсхолдерами
    return [res.get(i, f"Placeholder reward {i}") for i in range(1, 101)]


def _extract_mini_events_ws(ws) -> List[Dict[str, str]]:
    """
    Лист "Мини-ивенты": первая колонка.
    Строки вида "1. Название", дальше 1+ строк описания.
    """
    values = [r[0] for r in ws.iter_rows(values_only=True) if r and r[0] is not None]
    lines = [str(x) for x in values]
    events: List[Dict[str, str]] = []
    current = None
    first = True
    for line in lines:
        if first:
            first = False  # предполагаем, что первая строка — заголовок
            continue
        # Новая запись, если есть цифры и точка (например "1. Что-то")
        if any(ch.isdigit() for ch in line) and "." in line:
            if current:
                events.append(current)
            current = {"title": line.strip(), "text": ""}
        else:
            if current:
                if current["text"]:
                    current["text"] += " "
                current["text"] += line.strip()
    if current:
        events.append(current)
    return events


def _find_partner_indexes(rewards: List[str]) -> List[int]:
    """Ищем награды, связанные с парнем (чтобы слать ему уведомление)."""
    idxs: List[int] = []
    for i, s in enumerate(rewards, start=1):
        low = s.lower()
        if (
            "от него" in low
            or "свидание" in low
            or "он организует" in low
            or "он приготовит" in low
            or "он сделает" in low
        ):
            idxs.append(i)
    return idxs


LOOTBOX_REWARD_TABLES: Dict[int, List[str]] = {}
PARTNER_REWARD_INDEXES: Dict[int, List[int]] = {}
MINI_EVENTS: List[Dict[str, str]] = []


def load_lootboxes_from_excel():
    global LOOTBOX_REWARD_TABLES, PARTNER_REWARD_INDEXES, MINI_EVENTS

    logger.info("Загружаю лутбоксы и мини-ивенты из '%s'...", LOOTBOX_XLS_PATH)
    wb = load_workbook(LOOTBOX_XLS_PATH, data_only=True)

    ws1 = wb["1. Маленькое счастье"]
    ws2 = wb["2. Средний"]
    ws3 = wb["3. Большой"]
    ws4 = wb["4. Эпический"]
    ws5 = wb["5. Легендарный"]
    ws_mini = wb["Мини-ивенты"]

    rewards_1 = _extract_rewards_ws(ws1)
    rewards_2 = _extract_rewards_ws(ws2)
    rewards_3 = _extract_rewards_ws(ws3)
    rewards_4 = _extract_rewards_ws(ws4)
    rewards_5 = _extract_rewards_ws(ws5)

    LOOTBOX_REWARD_TABLES = {
        1: rewards_1,
        2: rewards_2,
        3: rewards_3,
        4: rewards_4,
        5: rewards_5,
    }

    PARTNER_REWARD_INDEXES = {
        1: _find_partner_indexes(rewards_1),
        2: _find_partner_indexes(rewards_2),
        3: _find_partner_indexes(rewards_3),
        4: _find_partner_indexes(rewards_4),
        5: _find_partner_indexes(rewards_5),
    }

    MINI_EVENTS = _extract_mini_events_ws(ws_mini)

    logger.info(
        "Лутбоксы и мини-ивенты загружены. Box1=%d, Box2=%d, Box3=%d, Box4=%d, Box5=%d, mini_events=%d",
        len(rewards_1),
        len(rewards_2),
        len(rewards_3),
        len(rewards_4),
        len(rewards_5),
        len(MINI_EVENTS),
    )


# ========= DAILY CATEGORIES (дейлики) =========

DAILY_CATEGORIES = {
    "small": {
        "label": "🟦 Маленькое задание",
        "coins": 2,
        "examples": [
            "Помыть одну тарелку/кружку.",
            "Сложить одну стопку одежды.",
            "Выкинуть мусор в одном ведре.",
            "Ответить на одно важное сообщение.",
            "Разобрать один маленький угол стола.",
            "Сделать 5 минут растяжки.",
            "Сходить за водой и выпить стакан.",
            "Записать одну мысль в заметки.",
            "Протереть одну поверхность.",
            "Сделать один маленький шаг по работе (написать письмо, открыть проект).",
        ],
    },
    "standard": {
        "label": "🟩 Стандартное задание",
        "coins": 4,
        "examples": [
            "25–40 минут фокусной работы.",
            "Приготовить простую еду дома.",
            "Протереть все поверхности в одной комнате.",
            "Разобрать одну полку/ящик.",
            "Сделать одну учебную/рабочую сессию по апворку.",
            "Сделать короткую прогулку 15–20 минут.",
            "Принять душ с полным уходом.",
            "Сделать заметку по финансам за день.",
            "Сделать короткий обзор задач на завтра.",
            "Поддерживающая уборка в зоне, которая сейчас важна.",
        ],
    },
    "unpleasant": {
        "label": "🟥 Неприятное/отложенное",
        "coins": 6,
        "examples": [
            "Разобраться с одной неприятной бумажкой/платежом.",
            "Написать сложное сообщение, которое давно откладываешь.",
            "Позвонить/написать в инстанцию, которую боишься.",
            "Сделать часть медицинского/официального дела.",
            "Разобрать один страшный угол с хламом.",
            "Сесть и честно посмотреть на цифры по деньгам.",
            "Разгрести почту, где давно бардак.",
            "Удалить лишние файлы/проекты, которые тянут энергию.",
            "Закрыть вкладку/идею, которую таскаешь, но не делаешь.",
            "Сделать шаг в задаче, от которой чувствуешь стыд/страх.",
        ],
    },
    "focus": {
        "label": "💡 Фокус-блок (глубокая работа)",
        "coins": 8,
        "examples": [
            "Один 50-минутный фокус-блок на апворк/проект.",
            "Один фокус-блок на финансовое планирование.",
            "Один фокус-блок на геймдизайн/рисование.",
            "Один фокус-блок на подготовку материалов для ремонта.",
            "Один фокус-блок на систематизацию файлов/папок.",
            "Один фокус-блок на обучение (курс, видео, практика).",
            "Один фокус-блок на большой рабочий проект.",
            "Один фокус-блок на «генеральную уборку» в одной комнате.",
            "Один фокус-блок на разбор апворк-профиля и откликов.",
            "Один фокус-блок на планирование следующего месяца.",
        ],
    },
}


# ========= БАЗА ДАННЫХ (PostgreSQL через pg8000) =========

def get_db():
    """
    Подключаемся к PostgreSQL через pg8000, парсим DATABASE_URL вручную.
    Примеры URL:
    - postgres://user:pass@host:port/dbname
    - postgresql://user:pass@host:port/dbname
    """
    url = urlparse(DATABASE_URL)
    user = url.username
    password = url.password
    host = url.hostname
    port = url.port or 5432
    database = url.path.lstrip("/")

    conn = pg8000.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now(),
            last_lootbox_opened_at TIMESTAMPTZ
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS main_quest_progress (
            user_id BIGINT,
            quest_id TEXT,
            completed BOOLEAN DEFAULT FALSE,
            completed_at TIMESTAMPTZ,
            PRIMARY KEY (user_id, quest_id)
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rewards_obtained (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            source TEXT,
            rarity TEXT,
            lootbox_type INTEGER,
            reward_text TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        """
    )

    conn.commit()
    conn.close()
    logger.info("Схема БД инициализирована.")


def ensure_user(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = %s;", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (user_id, coins) VALUES (%s, %s);",
            (user_id, 0),
        )
        conn.commit()
    conn.close()


def add_coins(user_id: int, amount: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (user_id, coins)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE
        SET coins = users.coins + EXCLUDED.coins;
        """,
        (user_id, amount),
    )
    conn.commit()
    conn.close()


def get_user_coins(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT coins FROM users WHERE user_id = %s;", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def reset_user(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rewards_obtained WHERE user_id = %s;", (user_id,))
    cur.execute("DELETE FROM main_quest_progress WHERE user_id = %s;", (user_id,))
    cur.execute("DELETE FROM users WHERE user_id = %s;", (user_id,))
    conn.commit()
    conn.close()


def mark_quest_completed(user_id: int, quest_id: str) -> Optional[MainQuest]:
    quest = MAIN_QUESTS.get(quest_id)
    if not quest:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO main_quest_progress (user_id, quest_id, completed, completed_at)
        VALUES (%s, %s, TRUE, now())
        ON CONFLICT (user_id, quest_id) DO UPDATE
        SET completed = TRUE, completed_at = EXCLUDED.completed_at;
        """,
        (user_id, quest_id),
    )
    conn.commit()
    conn.close()
    return quest


def user_quest_status(user_id: int) -> Dict[str, bool]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT quest_id, completed FROM main_quest_progress WHERE user_id = %s;",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    status: Dict[str, bool] = {}
    for quest_id, completed in rows:
        status[str(quest_id)] = bool(completed)
    return status


def add_reward_record(user_id: int, source: str, rarity: str, lootbox_type: int, reward_text: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rewards_obtained (user_id, source, rarity, lootbox_type, reward_text)
        VALUES (%s, %s, %s, %s, %s);
        """,
        (user_id, source, rarity, lootbox_type, reward_text),
    )
    conn.commit()
    conn.close()


def get_rewards_for_user(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, rarity, lootbox_type, reward_text, created_at
        FROM rewards_obtained
        WHERE user_id = %s
        ORDER BY created_at DESC;
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for source, rarity, lootbox_type, reward_text, created_at in rows:
        result.append(
            {
                "source": source,
                "rarity": rarity,
                "lootbox_type": lootbox_type,
                "reward_text": reward_text,
                "created_at": created_at,
            }
        )
    return result


# ========= ТЕКСТОВЫЕ АНИМАЦИИ И ПРОГРЕСС =========

def card_open_animation(quest: MainQuest) -> str:
    lines = [
        "✨ Карта-награда начала мерцать...",
        f"Редкость: *{quest.rarity}*",
        "Ты аккуратно поворачиваешь её в руках —",
        "и она мягко раскрывается, превращаясь в маленькое обещание о награде в реальном мире 💖",
        f"За квест *{quest.title}* ты получаешь карту редкости *{quest.rarity}*.",
        "Можешь взять одну бумажную карту этой редкости из своей стопки.",
    ]
    return "\n".join(lines)


def apartment_progress_bar(status: Dict[str, bool]) -> str:
    total = 0
    done = 0
    for q in MAIN_QUESTS.values():
        if q.level in (5, 6):
            total += 1
            if status.get(q.id):
                done += 1
    if total == 0:
        return "🏡 Уровень апартаментов: [----------] 0/0"
    ratio = done / total
    steps = 10
    filled = int(round(ratio * steps))
    bar = "█" * filled + "░" * (steps - filled)
    return f"🏡 Уровень апартаментов: [{bar}] {done}/{total}"


def lootbox_open_animation(box_type: int, reward_text: str) -> str:
    names = {
        1: "Little Happiness",
        2: "Middle",
        3: "Large",
        4: "Epic",
        5: "Legendary",
    }
    name = names.get(box_type, f"Лутбокс {box_type}")
    return (
        f"📦 Ты открываешь *{name}*...\n"
        f"Сверху слетают искорки, внутри что-то шуршит...\n\n"
        f"🎁 Выпало: *{reward_text}*"
    )


# ========= КНОПКИ И МЕНЮ =========

MAIN_MENU_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("📜 Мейн-квесты", callback_data="menu:quests")],
        [InlineKeyboardButton("📆 Дейлики", callback_data="menu:dailies")],
        [InlineKeyboardButton("💎 Лутбоксы", callback_data="menu:lootboxes")],
        [InlineKeyboardButton("🎒 Инвентарь/награды", callback_data="menu:rewards")],
        [InlineKeyboardButton("👤 Профиль", callback_data="menu:profile")],
    ]
)


# ========= ХЕНДЛЕРЫ =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id)
    text = (
        "Привет! Это твоя личная игра *LifeQuest*.\n\n"
        "⭐ У тебя есть мейн-квесты (долги, ремонт, Тбилиси).\n"
        "⭐ За каждый квест ты получаешь карту-награду и монеты.\n"
        "⭐ Монеты можно тратить на лутбоксы с наградами.\n\n"
        "Выбери, с чего начнём 👇"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=MAIN_MENU_KB, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=MAIN_MENU_KB, parse_mode="Markdown"
        )


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Главное меню:", reply_markup=MAIN_MENU_KB, parse_mode="Markdown")


# ---- Профиль ----

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    ensure_user(user.id)
    coins = get_user_coins(user.id)
    status = user_quest_status(user.id)
    rewards = get_rewards_for_user(user.id)

    level_progress = {lvl: {"done": 0, "total": 0} for lvl in range(0, 8)}
    for q in MAIN_QUESTS.values():
        level_progress[q.level]["total"] += 1
        if status.get(q.id):
            level_progress[q.level]["done"] += 1

    lines = [
        f"👤 *Профиль @{user.username or user.first_name}*",
        f"💰 Монеты: *{coins}*",
        "",
        "📊 Прогресс по уровням:",
    ]
    for lvl in range(0, 8):
        prog = level_progress[lvl]
        if prog["total"] == 0:
            continue
        lines.append(f"- Уровень {lvl}: {prog['done']} / {prog['total']}")

    lines.append("")
    lines.append(apartment_progress_bar(status))

    rarity_counts = {r: 0 for r in RARITY_ORDER}
    for row in rewards:
        r = row["rarity"]
        if r in rarity_counts:
            rarity_counts[r] += 1

    lines.append("")
    lines.append("🎁 Карты-награды и лутбоксы:")
    for r in RARITY_ORDER:
        lines.append(f"- {r}: {rarity_counts[r]} шт.")

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅ Назад", callback_data="menu:main")],
            [InlineKeyboardButton("♻ Сбросить прогресс", callback_data="profile:reset_confirm")],
        ]
    )

    await query.answer()
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def profile_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да, сбросить", callback_data="profile:reset_do"),
                InlineKeyboardButton("↩ Отмена", callback_data="menu:profile"),
            ]
        ]
    )
    await query.answer()
    await query.edit_message_text(
        "Точно сбросить *всю игру* и начать сначала?",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def profile_reset_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    reset_user(user.id)
    await query.answer("Прогресс сброшен")
    await start(update, context)


# ---- Мейн-квесты ----

def build_quests_keyboard(level: int, user_id: int) -> InlineKeyboardMarkup:
    status = user_quest_status(user_id)
    buttons = []
    for q in MAIN_QUESTS.values():
        if q.level != level:
            continue
        done = "✅" if status.get(q.id) else "⬜"
        buttons.append(
            [InlineKeyboardButton(f"{done} {q.id} {q.title}", callback_data=f"quest:{q.id}")]
        )
    buttons.append([InlineKeyboardButton("⬅ Назад", callback_data="menu:quests")])
    return InlineKeyboardMarkup(buttons)


async def quests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    ensure_user(user.id)

    kb_rows = []
    for lvl in range(0, 8):
        kb_rows.append(
            [InlineKeyboardButton(f"Уровень {lvl}", callback_data=f"quests_level:{lvl}")]
        )
    kb_rows.append([InlineKeyboardButton("⬅ Назад", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(kb_rows)

    await query.answer()
    await query.edit_message_text(
        "Выбери уровень мейн-квестов:", reply_markup=kb, parse_mode="Markdown"
    )


async def quests_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    _, lvl_str = query.data.split(":", 1)
    level = int(lvl_str)
    kb = build_quests_keyboard(level, user.id)
    await query.answer()
    await query.edit_message_text(
        f"Уровень {level} — мейн-квесты:", reply_markup=kb, parse_mode="Markdown"
    )


async def quest_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    _, qid = query.data.split(":", 1)
    quest = MAIN_QUESTS.get(qid)
    if not quest:
        await query.answer("Неизвестный квест")
        return

    status = user_quest_status(user.id)
    done = status.get(qid, False)
    lines = [
        f"*{quest.id} — {quest.title}*",
        "",
        quest.description,
        "",
        f"Редкость карты-награды: *{quest.rarity}*",
    ]
    buttons = [[InlineKeyboardButton("⬅ К уровням", callback_data="menu:quests")]]
    if not done:
        buttons.insert(
            0,
            [InlineKeyboardButton("✅ Отметить выполненным", callback_data=f"quest_complete:{qid}")],
        )
    kb = InlineKeyboardMarkup(buttons)

    await query.answer()
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def quest_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    _, qid = query.data.split(":", 1)
    quest = MAIN_QUESTS.get(qid)
    if not quest:
        await query.answer("Неизвестный квест")
        return

    status = user_quest_status(user.id)
    if status.get(qid):
        await query.answer("Этот квест уже выполнен")
        return

    mark_quest_completed(user.id, qid)
    coins = RARITY_TO_COINS.get(quest.rarity, 5)
    add_coins(user.id, coins)
    add_reward_record(
        user.id,
        source=f"quest:{qid}",
        rarity=quest.rarity,
        lootbox_type=0,
        reward_text=f"Карта {quest.rarity} за {quest.title}",
    )

    text = card_open_animation(quest) + f"\n\n💰 Ты получаешь *{coins}* монет."
    await query.answer("Квест закрыт!")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU_KB)


# ---- Дейлики ----

async def dailies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    ensure_user(user.id)
    coins = get_user_coins(user.id)

    lines = [
        "📆 *Ежедневные задания*",
        f"Текущий баланс: *{coins}* монет.",
        "",
        "Нажимая на кнопку, ты сообщаешь боту, что уже сделала одно задание такого типа.",
        "Он начислит монеты и предложит пример задания на будущее.",
        "",
        "Категории:",
    ]
    for key, cfg in DAILY_CATEGORIES.items():
        lines.append(f"- {cfg['label']} (+{cfg['coins']} монет)")

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(DAILY_CATEGORIES["small"]["label"], callback_data="daily:small")],
            [InlineKeyboardButton(DAILY_CATEGORIES["standard"]["label"], callback_data="daily:standard")],
            [InlineKeyboardButton(DAILY_CATEGORIES["unpleasant"]["label"], callback_data="daily:unpleasant")],
            [InlineKeyboardButton(DAILY_CATEGORIES["focus"]["label"], callback_data="daily:focus")],
            [InlineKeyboardButton("⬅ Назад", callback_data="menu:main")],
        ]
    )

    await query.answer()
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def daily_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    _, key = query.data.split(":", 1)
    cfg = DAILY_CATEGORIES.get(key)
    if not cfg:
        await query.answer("Неизвестная категория")
        return

    coins = cfg["coins"]
    add_coins(user.id, coins)
    example = random.choice(cfg["examples"])

    text = (
        f"{cfg['label']} засчитано!\n\n"
        f"💰 Начислено *{coins}* монет.\n\n"
        f"💡 Пример похожего задания на будущее:\n- {example}"
    )

    await query.answer(f"+{coins} монет")
    await query.edit_message_text(text, reply_markup=MAIN_MENU_KB, parse_mode="Markdown")


# ---- Лутбоксы ----

LOOTBOX_COSTS = {1: 10, 2: 20, 3: 40, 4: 80, 5: 150}


async def lootboxes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    coins = get_user_coins(user.id)

    lines = [
        "💎 *Лутбоксы*",
        f"У тебя сейчас: *{coins}* монет.",
        "",
        "Выбери, какой открыть (если хватает монет):",
    ]

    names = {
        1: "Little Happiness",
        2: "Middle",
        3: "Large",
        4: "Epic",
        5: "Legendary",
    }
    kb_rows = []
    for box_type in range(1, 6):
        cost = LOOTBOX_COSTS[box_type]
        kb_rows.append(
            [
                InlineKeyboardButton(
                    f"{names[box_type]} — {cost} монет",
                    callback_data=f"lootbox_open:{box_type}",
                )
            ]
        )
    kb_rows.append([InlineKeyboardButton("⬅ Назад", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(kb_rows)

    await query.answer()
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def lootbox_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    _, box_str = query.data.split(":", 1)
    box_type = int(box_str)

    ensure_user(user.id)
    coins = get_user_coins(user.id)
    cost = LOOTBOX_COSTS[box_type]
    if coins < cost:
        await query.answer("Недостаточно монет 😢")
        await query.edit_message_text(
            f"У тебя всего {coins} монет, а нужно {cost}.", reply_markup=MAIN_MENU_KB, parse_mode="Markdown"
        )
        return

    # списываем монеты
    add_coins(user.id, -cost)

    # d100
    table = LOOTBOX_REWARD_TABLES[box_type]
    roll = random.randint(1, 100)
    reward_text = table[roll - 1]

    if box_type == 1:
        rarity = "Common"
    elif box_type == 2:
        rarity = "Uncommon"
    elif box_type == 3:
        rarity = "Rare"
    elif box_type == 4:
        rarity = "Epic"
    else:
        rarity = "Legendary"

    add_reward_record(
        user.id,
        source=f"lootbox:{box_type}",
        rarity=rarity,
        lootbox_type=box_type,
        reward_text=reward_text,
    )

    text = lootbox_open_animation(box_type, reward_text)

    # Мини-ивент с шансом 25%
    if MINI_EVENTS and random.random() < 0.25:
        ev = random.choice(MINI_EVENTS)
        text += f"\n\n🎲 *Мини-ивент дня:* {ev['title']}\n{ev['text']}"

    # Уведомление парню, если награда "от него"
    partner_indices = PARTNER_REWARD_INDEXES.get(box_type, [])
    if PARTNER_USER_ID and (roll in partner_indices):
        try:
            await context.bot.send_message(
                chat_id=PARTNER_USER_ID,
                text=f"💌 Ви выбила награду, связанную с тобой (d100={roll}):\n\n{reward_text}",
            )
        except Exception as e:
            logger.warning("Не удалось отправить уведомление партнёру: %s", e)

    await query.answer(f"d100 = {roll}")
    await query.edit_message_text(text, reply_markup=MAIN_MENU_KB, parse_mode="Markdown")


# ---- Инвентарь ----

async def rewards_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    rewards = get_rewards_for_user(user.id)

    if not rewards:
        text = "Пока нет сохранённых наград. Закрой квест или открой лутбокс 💖"
    else:
        lines = ["🎒 *Твои награды* (последние 20):", ""]
        for row in rewards[:20]:
            src = row["source"]
            rarity = row["rarity"]
            lb_type = row["lootbox_type"]
            rtext = row["reward_text"]
            created = row["created_at"]
            if lb_type:
                lines.append(f"• [{created}] Лутбокс {lb_type} — *{rarity}*: {rtext}")
            else:
                lines.append(f"• [{created}] Квест — *{rarity}*: {rtext}")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="menu:main")]])
    await query.answer()
    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")


# ========= РОУТЕР CALLBACK-ДАННЫХ =========

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "menu:main":
        await main_menu(update, context)
    elif data == "menu:quests":
        await quests_menu(update, context)
    elif data.startswith("quests_level:"):
        await quests_level(update, context)
    elif data.startswith("quest_complete:"):
        await quest_complete(update, context)
    elif data.startswith("quest:"):
        await quest_detail(update, context)
    elif data == "menu:lootboxes":
        await lootboxes_menu(update, context)
    elif data.startswith("lootbox_open:"):
        await lootbox_open(update, context)
    elif data == "menu:rewards":
        await rewards_menu(update, context)
    elif data == "menu:profile":
        await profile(update, context)
    elif data == "profile:reset_confirm":
        await profile_reset_confirm(update, context)
    elif data == "profile:reset_do":
        await profile_reset_do(update, context)
    elif data == "menu:dailies":
        await dailies_menu(update, context)
    elif data.startswith("daily:"):
        await daily_done(update, context)
    else:
        await query.answer("Неизвестное действие")


# ========= MAIN / WEBHOOK =========

def main():
    load_lootboxes_from_excel()
    init_db()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))

    logger.info("Starting bot with webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
