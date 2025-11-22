import asyncio
import os
import random
import sqlite3
import zipfile
from datetime import datetime, date
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

from dotenv import load_dotenv
import re
import uuid
from collections import defaultdict

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import (
        Message,
        CallbackQuery,
        InlineKeyboardMarkup,
        InlineKeyboardButton,
    )
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
except ImportError:
    # Friendly runtime error if aiogram is not installed.
    # Install with: pip install aiogram
    print("Missing dependency 'aiogram'. Install it with: pip install aiogram")
    raise

# ================== НАСТРОЙКИ ==================

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
DB_PATH = "game_bot.db"

# Если хочешь сделать бота приватным — впиши сюда свой Telegram ID
# Узнать можно у @userinfobot
ALLOWED_USER_IDS = set()  # напр. {123456789}


# ================== БАЗА ДАННЫХ ==================

def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS users(
        user_id   INTEGER PRIMARY KEY,
        coins     INTEGER DEFAULT 0,
        created_at TEXT
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS rewards(
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER,
        name      TEXT,
        box_level INTEGER,
        used      INTEGER DEFAULT 0,
        created_at TEXT
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS main_progress(
        user_id    INTEGER,
        node_index INTEGER,
        status     TEXT,
        PRIMARY KEY(user_id, node_index)
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS daily_tasks(
        user_id   INTEGER,
        task_code TEXT,
        day       TEXT,
        done      INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, task_code, day)
    )
    """
    )

    conn.commit()
    conn.close()


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
            (user_id, 0, datetime.utcnow().isoformat()),
        )
        conn.commit()
        coins = 0
    else:
        coins = row[0]
    conn.close()
    return coins


def update_coins(user_id: int, delta: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO users(user_id, coins, created_at)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET coins = coins + ?
    """,
        (user_id, 0, datetime.utcnow().isoformat(), delta),
    )
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
    c.execute(
        """
        INSERT INTO main_progress(user_id, node_index, status)
        VALUES(?,?,?)
        ON CONFLICT(user_id, node_index) DO UPDATE SET status = ?
    """,
        (user_id, node_index, status, status),
    )
    conn.commit()
    conn.close()


def get_daily_done(user_id: int, task_code: str, day: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT done FROM daily_tasks
        WHERE user_id = ? AND task_code = ? AND day = ?
    """,
        (user_id, task_code, day),
    )
    row = c.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def set_daily_done(user_id: int, task_code: str, day: str, done: bool):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO daily_tasks(user_id, task_code, day, done)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id, task_code, day) DO UPDATE SET done = ?
    """,
        (user_id, task_code, day, 1 if done else 0, 1 if done else 0),
    )
    conn.commit()
    conn.close()


# ================== ИГРОВАЯ КОНФИГА ==================

LOOTBOXES = {
    1: {"name": "Little Happiness", "price": 10},
    2: {"name": "Medium Loot Box", "price": 20},
    3: {"name": "Large Loot Box", "price": 40},
    4: {"name": "Epic Loot Box", "price": 80},
    5: {"name": "Legendary Loot Box", "price": 150},
}

# Упрощённые d100-таблицы для лутбоксов (можешь позже вставить свои большие)
LOOTBOX_XLSX_CANDIDATES = ["lootbox.xlsx", "Лутбоксы.xlsx"]
TASKS_DOCX_CANDIDATES = [
    os.getenv("TASKS_DOCX"),
    "🎮 RLViGame_bot.docx",
]
DEFAULT_REWARD_TABLE = {
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
REWARD_TABLE = {lvl: list(entries) for lvl, entries in DEFAULT_REWARD_TABLE.items()}

# Карты-награды за Мейн-квесты
REWARD_CARDS = {
    "common": {"label": "🟦 Обычная карта награды"},
    "uncommon": {"label": "🟩 Необычная карта награды"},
    "rare": {"label": "🟪 Редкая карта награды"},
    "epic": {"label": "🟧 Эпическая карта награды"},
    "legendary": {"label": "🟥 Легендарная карта награды"},
}
RARITY_TO_BOX_LEVEL = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "epic": 4,
    "legendary": 5,
}

# Основные квесты — под твой реальный план
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

# Дейлики
DAILY_TASKS = {}

LEVEL_LABELS = {
    0: "🟣 УРОВЕНЬ 0 — СТАРТ",
    1: "🟢 УРОВЕНЬ 1 — НАЧАЛО ДВИЖЕНИЯ",
    2: "🔵 УРОВЕНЬ 2 — РАЗГОНЯЕМСЯ",
    3: "🟡 УРОВЕНЬ 3 — ПОДДЕРЖИВАЕМ РИТМ",
    4: "🔥 УРОВЕНЬ 4 — УСКОРЕНИЕ",
    5: "🛠 УРОВЕНЬ 5 — РЕМОНТНЫЙ МАРАФОН",
    6: "🏡 УРОВЕНЬ 6 — СДАЧА КВАРТИРЫ",
    7: "🚉 УРОВЕНЬ 7 — НАКОПЛЕНИЕ НА ТБИЛИСИ + ПЕРЕЕЗД",
}

# Метаданные уровней: даты и финальные награды
LEVEL_META = {
    0: {"dates": "20–25 ноября 2025", "final_coins": 0, "final_cards": []},
    1: {"dates": "20 ноября — 12 декабря 2025", "final_coins": 5, "final_cards": ["uncommon"]},
    2: {"dates": "12 декабря 2025 — 7 января 2026", "final_coins": 5, "final_cards": ["rare"]},
    3: {"dates": "7 января — 20 февраля 2026", "final_coins": 5, "final_cards": ["epic"]},
    4: {"dates": "20 февраля — 20 марта 2026", "final_coins": 10, "final_cards": ["legendary"]},
    5: {"dates": "20 марта — 20 апреля 2026", "final_coins": 15, "final_cards": ["epic", "legendary"]},
    6: {"dates": "20 апреля — 5 мая 2026", "final_coins": 10, "final_cards": ["legendary"]},
    7: {"dates": "5–31 мая 2026", "final_coins": 20, "final_cards": ["legendary"]},
}

# Зависимости квестов (код -> требуется выполнение кода)
QUEST_DEPENDENCIES = {
    "2.4": "2.3",
    "2.5": "2.4",
    "2.7": "2.6",
}
QUEST_CHOICES: Dict[int, Dict[str, Dict]] = {}

# Группы квестов для отображения (по образцу документа)
LEVEL_GROUPS = {
    2: [
        ("Хвосты", ["2.1"]),
        ("Долг 500$", ["2.2"]),
        ("Upwork", ["2.3", "2.4", "2.5"]),
        ("Финансы", ["2.6", "2.7"]),
        ("Ремонт", ["2.8"]),
    ],
}

# Расписание стартов уровней (блокирует до даты + до завершения прошлых уровней)
LEVEL_SCHEDULE = {
    0: {"start": date(2025, 11, 20), "end": date(2025, 11, 25)},
    1: {"start": date(2025, 11, 20), "end": date(2025, 12, 12)},
    2: {"start": date(2025, 12, 12), "end": date(2026, 1, 7)},
    3: {"start": date(2026, 1, 7), "end": date(2026, 2, 20)},
    4: {"start": date(2026, 2, 20), "end": date(2026, 3, 20)},
    5: {"start": date(2026, 3, 20), "end": date(2026, 4, 20)},
    6: {"start": date(2026, 4, 20), "end": date(2026, 5, 5)},
    7: {"start": date(2026, 5, 5), "end": date(2026, 5, 31)},
}

DAILY_SEARCH_WAIT: Dict[int, bool] = {}


def _excel_col_to_index(col: str) -> int:
    """Преобразует буквенный адрес столбца (A, B, AA...) в индекс с нуля."""
    idx = 0
    for ch in col:
        if not ch.isalpha():
            break
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1 if idx else 0


def _read_cell_value(cell, shared_strings, ns: str) -> str:
    """Возвращает текстовое значение ячейки (shared strings / inline / число)."""
    cell_type = cell.attrib.get("t")
    v = cell.find(f"{ns}v")
    if v is not None:
        if cell_type == "s":
            idx = int(v.text)
            return shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
        return v.text or ""
    inline = cell.find(f"{ns}is/{ns}t")
    return inline.text if inline is not None else ""


def _read_shared_strings(zf: zipfile.ZipFile, ns: str) -> List[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    strings = []
    for si in root.findall(f"{ns}si"):
        texts = [t.text or "" for t in si.findall(f".//{ns}t")]
        strings.append("".join(texts))
    return strings


def load_lootbox_reward_tables_from_excel(xlsx_path: str) -> Dict[int, List[Tuple[int, str]]]:
    """
    Читает lootbox.xlsx и собирает таблицы наград для уровней 1–5.
    Ожидается, что названия листов начинаются с «1. », «2. » и т.д.
    """
    if not os.path.exists(xlsx_path):
        return {}

    try:
        with zipfile.ZipFile(xlsx_path) as zf:
            ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            ns_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"

            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rel_map = {
                rel.attrib["Id"]: rel.attrib["Target"]
                for rel in rels.findall(f"{ns_rel}Relationship")
            }

            sheet_paths: Dict[int, str] = {}
            for sheet in workbook.findall(f".//{ns_main}sheet"):
                name = sheet.attrib.get("name", "")
                rid = sheet.attrib.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                if not rid or rid not in rel_map:
                    continue

                prefix = name.split(".", 1)[0].strip()
                if prefix.isdigit():
                    lvl = int(prefix)
                    if lvl in LOOTBOXES:
                        sheet_paths[lvl] = f"xl/{rel_map[rid]}"

            shared_strings = _read_shared_strings(zf, ns_main)
            reward_tables: Dict[int, List[Tuple[int, str]]] = {}

            for lvl, sheet_path in sheet_paths.items():
                try:
                    sheet_xml = ET.fromstring(zf.read(sheet_path))
                except KeyError:
                    continue

                rows = []
                for row in sheet_xml.findall(f"{ns_main}sheetData/{ns_main}row"):
                    row_values = {}
                    for cell in row.findall(f"{ns_main}c"):
                        ref = cell.attrib.get("r", "")
                        col_letters = "".join(ch for ch in ref if ch.isalpha())
                        col_idx = _excel_col_to_index(col_letters)
                        row_values[col_idx] = _read_cell_value(cell, shared_strings, ns_main)
                    rows.append([row_values.get(0, ""), row_values.get(1, "")])

                # ищем строку-заголовок с d100 и собираем данные ниже
                entries: List[Tuple[int, str]] = []
                header_seen = False
                for roll_raw, reward_name in rows:
                    if not header_seen:
                        if isinstance(roll_raw, str) and roll_raw.lower().startswith("d100"):
                            header_seen = True
                        continue

                    if not roll_raw or not reward_name:
                        continue
                    try:
                        roll_num = int(float(str(roll_raw)))
                    except ValueError:
                        continue
                    entries.append((roll_num, reward_name))

                if entries:
                    entries.sort(key=lambda x: x[0])
                    reward_tables[lvl] = entries

            return reward_tables
    except Exception as exc:
        print(f"Ошибка при чтении {xlsx_path}: {exc}")
        return {}


def refresh_reward_table():
    """Обновляет глобальную таблицу наград из Excel с откатом к дефолту."""
    global REWARD_TABLE
    env_path = os.getenv("LOOTBOX_XLSX")
    candidates = [env_path] + LOOTBOX_XLSX_CANDIDATES
    xlsx_path = next((p for p in candidates if p and os.path.exists(p)), candidates[1])

    loaded = load_lootbox_reward_tables_from_excel(xlsx_path)
    merged: Dict[int, List[Tuple[int, str]]] = {}
    for lvl in LOOTBOXES:
        if loaded.get(lvl):
            merged[lvl] = loaded[lvl]
        else:
            merged[lvl] = list(DEFAULT_REWARD_TABLE.get(lvl, []))
    REWARD_TABLE = merged

    if loaded:
        print(f"Награды лутбоксов загружены из {xlsx_path}")
    else:
        print("Используются встроенные награды лутбоксов")


def _load_docx_lines(docx_path: str) -> List[str]:
    """Возвращает список строк (абзацев) из docx."""
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
        if texts:
            lines.append("".join(texts))
    return lines


def load_main_quests_from_docx(docx_path: str) -> List[Dict]:
    """
    Парсит docx и достаёт мейн-квесты вида:
    '1.1 Название → Rare ×1 + 3 coin'
    Возвращает список с последовательной нумерацией для БД и оригинальным кодом.
    """
    try:
        lines = _load_docx_lines(docx_path)
    except Exception as exc:
        print(f"Не удалось прочитать docx для квестов: {exc}")
        return []

    pattern = re.compile(
        r"(?P<code>\d+\.\d+)\s+(?P<title>.+?)\s*→\s*(?P<rarity>[A-Za-zА-Яа-я]+)\s*×1\s*\+\s*(?P<coins>\d+)\s*coin",
        re.IGNORECASE,
    )

    quests = []
    seen = set()
    for line in lines:
        for m in pattern.finditer(line):
            rarity = m.group("rarity").strip().lower()
            rarity = {
                "common": "common",
                "uncommon": "uncommon",
                "rare": "rare",
                "epic": "epic",
                "legendary": "legendary",
            }.get(rarity, "common")
            key = (m.group("code"), m.group("title").strip())
            if key in seen:
                continue
            seen.add(key)
            quests.append(
                {
                    "code": m.group("code"),
                    "title": m.group("title").strip(),
                    "reward_coins": int(m.group("coins")),
                    "reward_card": rarity,
                }
            )

    for idx, q in enumerate(quests, start=1):
        q["index"] = idx
        q["desc"] = ""
    return quests


def load_daily_tasks_from_docx(docx_path: str) -> Dict[str, Dict]:
    """Читает docx и собирает категории 6.1–6.4 с монетами 1/2/3/5."""
    try:
        lines = _load_docx_lines(docx_path)
    except Exception as exc:
        print(f"Не удалось прочитать docx для дейликов: {exc}")
        return {}

    categories = [
        ("6.1", 1),
        ("6.2", 2),
        ("6.3", 3),
        ("6.4", 5),
    ]
    starts = {}
    for idx, line in enumerate(lines):
        for code, _coins in categories:
            if line.startswith(("● " + code, "▲ " + code, "★ " + code, "⏱ " + code)):
                starts[code] = idx

    tasks: Dict[str, Dict] = {}
    for code, coins in categories:
        if code not in starts:
            continue
        start_idx = starts[code] + 1
        next_indices = [i for c, i in starts.items() if i > starts[code]]
        end_idx = min(next_indices) if next_indices else len(lines)
        bucket: List[str] = []
        for offset, line in enumerate(lines[start_idx:end_idx]):
            if not line or "вариант" in line.lower() or "монет" in line.lower():
                continue
            # пропустим первые описательные строки после заголовка
            if offset < 2:
                continue
            text = line.strip()
            if not text:
                continue
            bucket.append(text)
        for i, title in enumerate(bucket, start=1):
            key = f"d{code.replace('.', '')}_{i}"
            tasks[key] = {"title": title, "coins": coins}
    return tasks


def _quest_level(q: Dict) -> int:
    code = q.get("code", "")
    if isinstance(code, str) and "." in code:
        try:
            return int(code.split(".", 1)[0])
        except ValueError:
            return 0
    return 0


def _quest_by_code(code: str) -> Dict | None:
    return next((q for q in MAIN_QUESTS if q.get("code") == code), None)


def _prev_levels_done(uid: int, lvl: int) -> bool:
    for q in MAIN_QUESTS:
        if _quest_level(q) < lvl and get_main_status(uid, q["index"]) != "done":
            return False
    return True


def _is_level_open(uid: int, lvl: int, today: date | None = None) -> bool:
    today = today or date.today()
    schedule = LEVEL_SCHEDULE.get(lvl)
    if schedule:
        start = schedule.get("start")
        if start and today < start:
            return False
    if not _prev_levels_done(uid, lvl):
        return False
    return True


def _quest_dependency_met(uid: int, quest: Dict) -> bool:
    code = quest.get("code")
    if not code:
        return True
    dep = QUEST_DEPENDENCIES.get(code)
    if not dep:
        return True
    prev = _quest_by_code(dep)
    if not prev:
        return True
    return get_main_status(uid, prev["index"]) == "done"


def _ensure_unlocks(uid: int):
    """Активирует все квесты, у которых выполнены зависимости и уровень открыт."""
    today = date.today()
    for q in MAIN_QUESTS:
        lvl = _quest_level(q)
        if not _is_level_open(uid, lvl, today=today):
            continue
        status = get_main_status(uid, q["index"])
        if status == "locked" and _quest_dependency_met(uid, q):
            set_main_status(uid, q["index"], "active")


def _grant_level_final(uid: int, lvl: int):
    meta = LEVEL_META.get(lvl)
    if not meta:
        return
    quests = [q for q in MAIN_QUESTS if _quest_level(q) == lvl]
    if not quests:
        return
    if not all(get_main_status(uid, q["index"]) == "done" for q in quests):
        return

    # Проверим, выдавали ли финал ранее (по записи в rewards)
    final_marker = f"ФИНАЛ {lvl}"
    existing = [r for r in get_active_rewards(uid) if final_marker in r[1]]
    if existing:
        return

    coins = meta.get("final_coins", 0)
    if coins:
        update_coins(uid, coins)
    for rarity in meta.get("final_cards", []):
        card_cfg = REWARD_CARDS.get(rarity, REWARD_CARDS["common"])
        add_reward(uid, f"{final_marker}: {card_cfg['label']}", 0)

    print(f"Выдан финал уровня {lvl} пользователю {uid}: +{coins} монет, карты {meta.get('final_cards')}")


def reset_user_progress(uid: int):
    conn = get_conn()
    c = conn.cursor()
    for table in ("users", "rewards", "main_progress", "daily_tasks"):
        c.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    coins = get_or_create_user(uid)
    _ensure_unlocks(uid)
    return coins


def level_progress(uid: int) -> str:
    levels = {}
    for q in MAIN_QUESTS:
        lvl = _quest_level(q)
        levels.setdefault(lvl, []).append(q)
    current_lvl = None
    for lvl in sorted(levels):
        if not all(get_main_status(uid, q["index"]) == "done" for q in levels[lvl]):
            current_lvl = lvl
            break
    if current_lvl is None:
        current_lvl = max(levels) if levels else 0
    quests = levels.get(current_lvl, [])
    done = sum(1 for q in quests if get_main_status(uid, q["index"]) == "done")
    total = len(quests)
    title = LEVEL_LABELS.get(current_lvl, f"Уровень {current_lvl}")
    return f"{title}: {done}/{total} квестов"


def refresh_tasks_from_docx():
    """Обновляет MAIN_QUESTS и DAILY_TASKS из docx, иначе оставляет дефолты."""
    global MAIN_QUESTS, DAILY_TASKS
    docx_path = next((p for p in TASKS_DOCX_CANDIDATES if p and os.path.exists(p)), None)
    if not docx_path:
        print("Docx с квестами/дейликами не найден, используются дефолты")
        return

    main_quests = load_main_quests_from_docx(docx_path)
    if main_quests:
        MAIN_QUESTS = main_quests
        print(f"Мейн-квесты загружены из {docx_path}: {len(MAIN_QUESTS)} шт.")
    else:
        print("Не удалось загрузить мейн-квесты из docx, дефолтные.")

    daily = load_daily_tasks_from_docx(docx_path)
    if daily:
        DAILY_TASKS = daily
        print(f"Дейлики загружены из {docx_path}: {len(DAILY_TASKS)} шт.")
    else:
        print("Не удалось загрузить дейлики из docx, дефолтные.")

# ================== ВСПОМОГАТЕЛЬНЫЕ ОТРИСОВКИ ==================


def build_map_view(uid: int) -> Tuple[str, InlineKeyboardMarkup]:
    _ensure_unlocks(uid)
    levels = {}
    for q in MAIN_QUESTS:
        lvl = _quest_level(q)
        levels.setdefault(lvl, []).append(q)

    kb = []
    lines = ["📍 <b>Квест-карта</b>\n"]
    for lvl in sorted(levels):
        quests = levels[lvl]
        statuses = []
        level_open = _is_level_open(uid, lvl)
        for q in quests:
            st = get_main_status(uid, q["index"])
            if not level_open:
                st = "locked"
            statuses.append(st)
        if all(s == "done" for s in statuses):
            mark = "✅"
        elif any(s == "active" for s in statuses):
            mark = "🟡"
        else:
            mark = "🔒"
        title = LEVEL_LABELS.get(lvl, f"Уровень {lvl}")
        date_range = LEVEL_META.get(lvl, {}).get("dates", "")
        date_label = f" ({date_range})" if date_range else ""
        lines.append(f"{mark} {title}{date_label}")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"Открыть {title[:28]}",
                    callback_data=f"level:{lvl}",
                )
            ]
        )

    kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


def build_profile_view(uid: int) -> Tuple[str, InlineKeyboardMarkup]:
    coins = get_coins(uid)
    progress = level_progress(uid)
    text = (
        f"💰 Монет: <b>{coins}</b>\n"
        f"🏃 Прогресс: {progress}\n\n"
        "Сбросит игру: удалит монеты, награды и прогресс квестов."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить игру", callback_data="reset:ask")],
            [InlineKeyboardButton(text="⬅ К карте", callback_data="menu:map")],
        ]
    )
    return text, kb


def build_dailies_view(uid: int, filter_coin: str = "all", search_term: str = "") -> Tuple[str, InlineKeyboardMarkup]:
    today = date.today().isoformat()
    lines = ["📝 <b>Дейлики на сегодня</b>"]
    kb_filters = [
        InlineKeyboardButton(text="Все", callback_data="dailies:filter:all"),
        InlineKeyboardButton(text="1 мон", callback_data="dailies:filter:1"),
        InlineKeyboardButton(text="2 мон", callback_data="dailies:filter:2"),
        InlineKeyboardButton(text="3 мон", callback_data="dailies:filter:3"),
        InlineKeyboardButton(text="5 мон", callback_data="dailies:filter:5"),
        InlineKeyboardButton(text="🔍 Поиск", callback_data="dailies:search"),
    ]

    tasks = DAILY_TASKS.items()
    if filter_coin != "all":
        try:
            cval = int(filter_coin)
            tasks = [(k, v) for k, v in tasks if v.get("coins") == cval]
            lines.append(f"Фильтр: {cval} монет")
        except ValueError:
            pass
    if search_term:
        tasks = [(k, v) for k, v in tasks if search_term.lower() in v.get("title", "").lower()]
        lines.append(f"Поиск: “{search_term}”")

    kb = [kb_filters[:3], kb_filters[3:]]
    for code, info in tasks:
        done = get_daily_done(uid, code, today)
        mark = "✅" if done else "⬜"
        lines.append(f"{mark} {info['title']} (+{info['coins']} монет)")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:18]}…",
                    callback_data=f"daily:{code}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


def roll_reward(box_level: int) -> str:
    return roll_single_reward(box_level)


def roll_single_reward(box_level: int) -> str:
    roll = random.randint(1, 100)
    table = REWARD_TABLE.get(box_level) or DEFAULT_REWARD_TABLE.get(box_level, [])
    for threshold, name in table:
        if roll <= threshold:
            return f"{name} (d100={roll})"
    return f"Сюрприз (d100={roll})"


def pick_rewards(box_level: int, count: int = 3) -> List[str]:
    table = REWARD_TABLE.get(box_level) or DEFAULT_REWARD_TABLE.get(box_level, [])
    names = [name for _, name in table]
    if not names:
        return []
    # случайная выборка с возможными повторами, но чаще всего разные
    return random.sample(names, k=min(count, len(names)))


def resolve_combo_reward(base_name: str, box_level: int) -> Tuple[str, List[str]]:
    """Если награда комбинированная — докидывает доп. roll'ы и возвращает список предметов."""
    lower = base_name.lower()
    if ("комбо" in lower) or ("+" in base_name):
        parts = base_name.count("+") + 1
        rolls = [roll_single_reward(box_level) for _ in range(parts)]
        return base_name, rolls
    return base_name, [base_name]


# ================== TELEGRAM-БОТ ==================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
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


def reply_menu_kb():
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📍 Квест-карта"),
                KeyboardButton(text="📝 Дейлики"),
            ],
            [
                KeyboardButton(text="🎁 Лутбоксы"),
                KeyboardButton(text="📦 Инвентарь"),
            ],
            [
                KeyboardButton(text="💰 Профиль"),
            ],
        ],
        resize_keyboard=True,
    )


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
    await message.answer(text, reply_markup=reply_menu_kb())


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if access_denied(message.from_user.id):
        await message.answer("Этот бот приватный 🌙")
        return

    coins = get_coins(message.from_user.id)
    await message.answer(
        f"🏠 <b>Главное меню</b>\nМонет: <b>{coins}</b>",
        reply_markup=reply_menu_kb(),
    )


@dp.message(F.text.in_({"📍 Квест-карта", "📝 Дейлики", "🎁 Лутбоксы", "📦 Инвентарь", "💰 Профиль"}))
async def on_menu_buttons(message: Message):
    if access_denied(message.from_user.id):
        await message.answer("Этот бот приватный 🌙")
        return
    text = message.text
    if text == "📍 Квест-карта":
        view_text, kb = build_map_view(message.from_user.id)
        await message.answer(view_text, reply_markup=kb)
    elif text == "📝 Дейлики":
        view_text, kb = build_dailies_view(message.from_user.id)
        await message.answer(view_text, reply_markup=kb)
    elif text == "🎁 Лутбоксы":
        uid = message.from_user.id
        coins = get_coins(uid)
        text = "🎁 <b>Лутбоксы</b>\n\n"
        for lvl, box in LOOTBOXES.items():
            text += f"{lvl}. {box['name']} — <b>{box['price']}</b> монет\n"
        text += (
            f"\nУ тебя сейчас <b>{coins}</b> монет.\nВыбери лутбокс, чтобы купить и открыть."
        )
        kb = []
        for lvl, box in LOOTBOXES.items():
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{lvl}. {box['name']}",
                        callback_data=f"buy:{lvl}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    elif text == "📦 Инвентарь":
        uid = message.from_user.id
        rewards = get_active_rewards(uid)
        if not rewards:
            text = (
                "📦 Твой инвентарь пока пуст.\n\n"
                "Заработай монеты за квесты или дейлики и открой лутбокс 🎁\n"
                "Или получи карту-награду за Мейн-квест."
            )
            kb = [
                [
                    InlineKeyboardButton(
                        text="🎁 К лутбоксам", callback_data="menu:loot"
                    )
                ],
                [InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")],
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
            kb.append(
                [InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")]
            )
            text = "\n".join(lines)
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    elif text == "💰 Профиль":
        profile_text, kb = build_profile_view(message.from_user.id)
        await message.answer(profile_text, reply_markup=kb)


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
        text, kb = build_map_view(uid)
        await callback.message.edit_text(
            text,
            reply_markup=kb,
        )

    # ДЕЙЛИКИ
    elif section == "dailies":
        text, kb = build_dailies_view(uid)
        await callback.message.edit_text(text, reply_markup=kb)

    # ЛУТБОКСЫ
    elif section == "loot":
        coins = get_coins(uid)
        text = "🎁 <b>Лутбоксы</b>\n\n"
        for lvl, box in LOOTBOXES.items():
            text += f"{lvl}. {box['name']} — <b>{box['price']}</b> монет\n"
        text += (
            f"\nУ тебя сейчас <b>{coins}</b> монет.\nВыбери лутбокс, чтобы купить и открыть."
        )

        kb = []
        for lvl, box in LOOTBOXES.items():
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{lvl}. {box['name']}",
                        callback_data=f"buy:{lvl}",
                    )
                ]
            )
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
                [
                    InlineKeyboardButton(
                        text="🎁 К лутбоксам", callback_data="menu:loot"
                    )
                ],
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
                kb.append(
                    [
                        InlineKeyboardButton(
                            text=f"Использовать: {name[:18]}…",
                            callback_data=f"use:{rid}",
                        )
                    ]
                )
            kb.append(
                [InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")]
            )
            text = "\n".join(lines)

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )

    # ПРОФИЛЬ / ГЛАВНОЕ МЕНЮ
    elif section in ("profile", "root"):
        text, kb = build_profile_view(uid)
        await callback.message.edit_text(
            text,
            reply_markup=kb,
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

    if not _quest_dependency_met(uid, quest):
        await callback.answer("Сначала заверши предыдущий квест в категории", show_alert=True)
        return

    status = get_main_status(uid, idx)
    if status == "locked":
        await callback.answer("Этот квест ещё закрыт 🔒", show_alert=True)
        return

    # Анимация движения по карте
    await show_path_animation(callback.message, quest["title"])

    label = quest.get("code", str(idx))
    desc = quest.get("desc") or ""
    parts = [
        f"📖 <b>Квест {label}: {quest['title']}</b>",
    ]
    if desc:
        parts.append(desc)
    card_label = REWARD_CARDS[quest["reward_card"]]["label"]
    box_lvl = RARITY_TO_BOX_LEVEL.get(quest["reward_card"], 1)
    parts.append(
        f"Награда: <b>{quest['reward_coins']}</b> монет и выбор 1 награды "
        f"из лутбокса L{box_lvl} ({card_label})."
    )
    text = "\n\n".join(parts)
    kb = [
        [
            InlineKeyboardButton(
                text="✅ Я это сделала", callback_data=f"quest_done:{idx}"
            )
        ],
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
    # квесты, зависящие от этого кода
    for code, dep in QUEST_DEPENDENCIES.items():
        if dep == quest.get("code"):
            nxt = _quest_by_code(code)
            if nxt and get_main_status(uid, nxt["index"]) == "locked":
                set_main_status(uid, nxt["index"], "active")
    _ensure_unlocks(uid)

    # награда монетами
    coins_reward = quest["reward_coins"]
    update_coins(uid, coins_reward)

    # выбор награды из соответствующего лутбокса
    box_level = RARITY_TO_BOX_LEVEL.get(quest["reward_card"], 1)
    options = pick_rewards(box_level, 3)
    token = uuid.uuid4().hex[:8]
    QUEST_CHOICES.setdefault(uid, {})[token] = {
        "options": options,
        "box_level": box_level,
    }

    _grant_level_final(uid, _quest_level(quest))

    parts = [
        f"🎉 <b>Квест {quest.get('code', idx)} выполнен!</b>",
        f"Ты получила <b>{coins_reward}</b> монет.",
        f"Выбери 1 из 3 наград лутбокса L{box_level}:",
    ]
    kb = []
    for i, opt in enumerate(options, start=1):
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {opt[:40]}",
                    callback_data=f"questpick:{token}:{i-1}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])
    await callback.message.answer(
        "\n\n".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("questpick:"))
async def cb_pick_reward(callback: CallbackQuery):
    uid = callback.from_user.id
    try:
        _, token, idx_str = callback.data.split(":", 2)
        opt_idx = int(idx_str)
    except Exception:
        await callback.answer("Неверный выбор", show_alert=True)
        return

    user_choices = QUEST_CHOICES.get(uid, {})
    payload = user_choices.get(token)
    if not payload:
        await callback.answer("Выбор недоступен", show_alert=True)
        return

    options = payload.get("options", [])
    if not (0 <= opt_idx < len(options)):
        await callback.answer("Неверный выбор", show_alert=True)
        return

    reward_name = options[opt_idx]
    box_level = payload.get("box_level", 0)
    add_reward(uid, reward_name, box_level)

    # очистить выбор, чтобы нельзя было брать многократно
    user_choices.pop(token, None)
    if not user_choices:
        QUEST_CHOICES.pop(uid, None)

    await callback.answer("Награда добавлена в инвентарь ✨", show_alert=False)
    await callback.message.answer(
        f"🏆 Ты выбрала: <b>{reward_name}</b>\nНаграда добавлена в инвентарь. /menu"
    )


@dp.callback_query(F.data.startswith("level:"))
async def cb_level(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    try:
        lvl = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    if not _is_level_open(uid, lvl):
        schedule = LEVEL_SCHEDULE.get(lvl, {})
        start = schedule.get("start")
        start_txt = f"Уровень откроется {start.isoformat()}" if start else "Уровень пока закрыт"
        await callback.answer(start_txt, show_alert=True)
        return

    quests = [q for q in MAIN_QUESTS if _quest_level(q) == lvl]
    if not quests:
        await callback.answer("Нет квестов для уровня", show_alert=True)
        return

    meta = LEVEL_META.get(lvl, {})
    date_range = meta.get("dates", "")
    lines = [LEVEL_LABELS.get(lvl, f"Уровень {lvl}")]
    if date_range:
        lines.append(f"⏳ {date_range}")
    final_line = []
    if meta.get("final_coins") or meta.get("final_cards"):
        rewards_txt = []
        coins = meta.get("final_coins", 0)
        if coins:
            rewards_txt.append(f"+{coins} coin")
        for r in meta.get("final_cards", []):
            rewards_txt.append(REWARD_CARDS.get(r, REWARD_CARDS['common'])['label'])
        final_line.append("🎯 Финал: " + " + ".join(rewards_txt))
    if final_line:
        lines.append("\n".join(final_line))
    lines.append("")
    kb = []
    groups = LEVEL_GROUPS.get(lvl)
    listed_ids = set()

    def add_q(q):
        status = get_main_status(uid, q["index"])
        if status != "done" and not _quest_dependency_met(uid, q):
            status = "locked"
        if status == "done":
            mark = "✅"
        elif status == "active":
            mark = "🟡"
        else:
            mark = "🔒"
        label = q.get("code", str(q["index"]))
        lines.append(f"{mark} {label}. {q['title']}")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"Открыть {label}", callback_data=f"quest:{q['index']}"
                )
            ]
        )
        listed_ids.add(q["index"])

    if groups:
        for name, codes in groups:
            lines.append(f"<b>{name}</b>")
            for code in codes:
                q = _quest_by_code(code)
                if q:
                    add_q(q)
            lines.append("")
    # Остальные квесты, если есть
    for q in quests:
        if q["index"] not in listed_ids:
            add_q(q)

    kb.append([InlineKeyboardButton(text="⬅ К карте", callback_data="menu:map")])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@dp.callback_query(F.data == "reset:ask")
async def cb_reset_ask(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, сбросить", callback_data="reset:do"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu:profile")],
        ]
    )
    await callback.message.edit_text(
        "Сбросить игру? Будут удалены монеты, прогресс квестов и награды.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data == "reset:do")
async def cb_reset_do(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return
    coins = reset_user_progress(uid)
    _ensure_unlocks(uid)
    await callback.message.edit_text(
        f"Игра сброшена. Монет: {coins}. Прогресс очищен.\n/menu",
        reply_markup=reply_menu_kb(),
    )
    await callback.answer("Сброшено")


@dp.message(F.text & (lambda msg: msg.from_user.id in DAILY_SEARCH_WAIT))
async def on_daily_search(message: Message):
    uid = message.from_user.id
    DAILY_SEARCH_WAIT.pop(uid, None)
    query = message.text.strip()
    if not query or query.startswith("/"):
        await message.answer("Поиск отменён.")
        return
    text, kb = build_dailies_view(uid, search_term=query)
    await message.answer(text, reply_markup=kb)


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
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{'Отменить' if done else 'Сделать'}: {info['title'][:14]}…",
                    callback_data=f"daily:{c}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="⬅ В меню", callback_data="menu:profile")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


@dp.callback_query(F.data.startswith("dailies:"))
async def cb_dailies_filter(callback: CallbackQuery):
    uid = callback.from_user.id
    if access_denied(uid):
        await callback.answer("Этот бот приватный 🌙", show_alert=True)
        return

    action = callback.data.split(":", 2)[1:]
    filter_coin = "all"
    search_term = ""
    if len(action) >= 2 and action[0] == "filter":
        filter_coin = action[1]
    elif len(action) >= 1 and action[0] == "search":
        DAILY_SEARCH_WAIT[uid] = True
        await callback.answer()
        await callback.message.answer("🔍 Введи текст для поиска дейликов (или /cancel)")
        return

    text, kb = build_dailies_view(uid, filter_coin=filter_coin, search_term=search_term)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


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
    refresh_reward_table()
    refresh_tasks_from_docx()
    init_db()
    # Очистим возможный вебхук, чтобы polling не конфликтовал с другими инстансами.
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
