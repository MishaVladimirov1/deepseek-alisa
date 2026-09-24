import os
import re
import io
import time
import json
import copy
import urllib.parse
import httpx

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse


app = FastAPI()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_API_URL = "https://api.tavily.com/search"

TELEGRAM_CONTACTS_JSON = os.getenv("TELEGRAM_CONTACTS_JSON", "{}")

try:
    TELEGRAM_CONTACTS = json.loads(TELEGRAM_CONTACTS_JSON)
    if not isinstance(TELEGRAM_CONTACTS, dict):
        TELEGRAM_CONTACTS = {}
except Exception:
    TELEGRAM_CONTACTS = {}


SERVER_SESSIONS = {}

GLOBAL_PROFILE = {
    "facts": [
        "пользователь находится в Краснодаре",
        "для покупок учитывать доставку или наличие в Краснодаре"
    ],
    "contacts": {},
    "telegram_contacts": TELEGRAM_CONTACTS,
    "notes": [],
    "tasks": []
}


MODES = {
    "обычный": {
        "title": "Обычный режим",
        "prompt": """
Ты голосовой AI-ассистент пользователя внутри навыка Алисы.
Отвечай по-русски, понятно, дружелюбно и не слишком длинно.
Если вопрос простой — отвечай сам.
Если есть данные из интернета — используй их.
Не называй себя ChatGPT.
""",
        "max_tokens": 500,
        "temperature": 0.7
    },

    "кратко": {
        "title": "Краткий режим",
        "prompt": """
Ты голосовой ассистент. Отвечай максимально коротко: 1-3 предложения.
Без воды. Только суть.
""",
        "max_tokens": 220,
        "temperature": 0.5
    },

    "джарвис": {
        "title": "Режим Джарвис",
        "prompt": """
Ты персональный ассистент в стиле умного дворецкого и аналитика.
Стиль: спокойно, чётко, уверенно, немного кинематографично, но без перебора.
Отвечай по-русски.
Если пользователь просит сделать действие — помогай выполнить его через доступные инструменты.
""",
        "max_tokens": 600,
        "temperature": 0.7
    },

    "исследователь": {
        "title": "Режим исследователя",
        "prompt": """
Ты исследователь и аналитик.
Если есть данные из интернета, анализируй их критически.
Делай выводы, сравнивай источники, выделяй главное.
Отвечай по-русски.
""",
        "max_tokens": 850,
        "temperature": 0.5
    },

    "оператор": {
        "title": "Режим оператора",
        "prompt": """
Ты режим оператора.
Говори очень кратко.
Если задача выполнена, отвечай: сделано, отправила, нашла, подготовила.
Подробности лучше отправлять в Telegram.
""",
        "max_tokens": 260,
        "temperature": 0.4
    },

    "секретарь": {
        "title": "Режим секретаря",
        "prompt": """
Ты личный секретарь пользователя.
Помогай составлять сообщения, документы, списки, планы, напоминания.
Пиши аккуратно и структурированно.
Отвечай по-русски.
""",
        "max_tokens": 800,
        "temperature": 0.6
    },

    "покупки": {
        "title": "Режим покупок",
        "prompt": """
Ты помощник по покупкам.
Помогай искать товары, сравнивать варианты, проверять риски, составлять список.
Учитывай город Краснодар и доставку в Краснодар.
Не оформляй и не оплачивай заказы самостоятельно.
Не выдумывай цены и ссылки.
""",
        "max_tokens": 800,
        "temperature": 0.5
    },

    "дом": {
        "title": "Режим дом",
        "prompt": """
Ты домашний помощник.
Помогай с рецептами, бытовыми задачами, ремонтом, покупками, списками дел и инструкциями.
Отвечай просто и практично.
""",
        "max_tokens": 700,
        "temperature": 0.6
    },

    "критик": {
        "title": "Режим критика",
        "prompt": """
Ты строгий, но полезный критик.
Ищи слабые места в идеях пользователя.
Говори прямо, но не грубо.
После критики предлагай улучшения.
""",
        "max_tokens": 650,
        "temperature": 0.6
    },

    "учитель": {
        "title": "Режим учителя",
        "prompt": """
Ты добрый учитель.
Объясняй простыми словами, с примерами.
Если тема сложная — разбивай на шаги.
""",
        "max_tokens": 700,
        "temperature": 0.6
    },

    "совет": {
        "title": "Режим совет директоров",
        "prompt": """
Ты моделируешь совет директоров из нескольких экспертов:
1. Стратег.
2. Финансист.
3. Маркетолог.
4. Технический эксперт.
5. Критик рисков.

На сложные вопросы отвечай структурой:
Стратег: ...
Финансист: ...
Маркетолог: ...
Технический эксперт: ...
Критик рисков: ...
Итог: ...

Отвечай по-русски, практично и без лишней воды.
""",
        "max_tokens": 900,
        "temperature": 0.7
    }
}


# ---------------------------------------------------------
# БАЗА
# ---------------------------------------------------------

def normalize_text(text):
    text = (text or "").lower().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s\+\-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def create_empty_state():
    return {
        "mode": "обычный",
        "facts": [],
        "history": [],
        "last_result": "",
        "last_links": [],
        "shopping_list": [],
        "pending_task": None,
        "created_at": time.time(),
        "last_seen": time.time()
    }


def cleanup_old_sessions():
    now = time.time()
    max_age = 6 * 60 * 60
    old = []

    for session_id, state in SERVER_SESSIONS.items():
        if now - state.get("last_seen", 0) > max_age:
            old.append(session_id)

    for session_id in old:
        del SERVER_SESSIONS[session_id]


def get_state(session_id, is_new=False):
    cleanup_old_sessions()

    if not session_id:
        session_id = "unknown_session"

    if is_new or session_id not in SERVER_SESSIONS:
        SERVER_SESSIONS[session_id] = create_empty_state()

    SERVER_SESSIONS[session_id]["last_seen"] = time.time()

    return SERVER_SESSIONS[session_id]


def save_state(session_id, state):
    if not session_id:
        session_id = "unknown_session"

    state["last_seen"] = time.time()
    SERVER_SESSIONS[session_id] = state


def make_response(text, end_session=False):
    if not text:
        text = "Готово."

    text = str(text).strip()

    if len(text) > 950:
        text = text[:950] + "..."

    return {
        "version": "1.0",
        "response": {
            "text": text,
            "tts": text,
            "end_session": end_session
        }
    }


# ---------------------------------------------------------
# РЕЖИМЫ
# ---------------------------------------------------------

def normalize_mode_name(text):
    text = normalize_text(text)

    aliases = {
        "обычный": "обычный",
        "стандартный": "обычный",

        "кратко": "кратко",
        "коротко": "кратко",
        "короткий": "кратко",

        "джарвис": "джарвис",
        "джервис": "джарвис",
        "джарвес": "джарвис",
        "jarvis": "джарвис",

        "исследователь": "исследователь",
        "аналитик": "исследователь",
        "поиск": "исследователь",

        "оператор": "оператор",

        "секретарь": "секретарь",

        "покупки": "покупки",
        "покупатель": "покупки",
        "товары": "покупки",
        "магазины": "покупки",

        "дом": "дом",
        "домашний": "дом",

        "критик": "критик",
        "критика": "критик",

        "учитель": "учитель",
        "учителя": "учитель",
        "преподаватель": "учитель",

        "совет": "совет",
        "совет директоров": "совет",
        "директоров": "совет"
    }

    return aliases.get(text, text)


def detect_mode_command(command):
    text = normalize_text(command)

    direct = normalize_mode_name(text)
    if direct in MODES:
        return direct

    prefixes = [
        "режим",
        "включи режим",
        "активируй режим",
        "переключись на режим",
        "переключись в режим",
        "поставь режим"
    ]

    for prefix in prefixes:
        if text.startswith(prefix):
            mode_part = text.replace(prefix, "", 1).strip()
            mode_name = normalize_mode_name(mode_part)
            if mode_name in MODES:
                return mode_name

    return None


def help_text():
    return """
Команды:
режим джарвис,
режим оператор,
режим исследователь,
режим секретарь,
режим покупки,
режим дом,
режим совет.

Интернет и Telegram:
найди в интернете ...
подбери самые дешёвые ...
скинь в телеграм
сделай документ
сделай таблицу
найди рядом

Заметки:
сохрани заметку ...
покажи заметки
найди в заметках ...

Задачи:
добавь задачу ...
покажи задачи
очисти задачи

Память:
запомни, что ...
что ты помнишь

Телефон:
запомни телефон мамы +79991234567
позвони маме
"""


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def get_telegram_recipient_chat_id(command=None):
    default_chat_id = TELEGRAM_CHAT_ID

    if not command:
        return default_chat_id

    text = normalize_text(command)

    contacts = {}
    contacts.update(GLOBAL_PROFILE.get("telegram_contacts", {}))

    normalized_contacts = {}

    for name, chat_id in contacts.items():
        normalized_contacts[normalize_text(name)] = str(chat_id)

    for name, chat_id in normalized_contacts.items():
        if name and name in text:
            return chat_id

    if "жене" in text or "жена" in text or "супруге" in text:
        for key in ["жене", "жена", "супруга", "супруге"]:
            if key in normalized_contacts:
                return normalized_contacts[key]

    if "маме" in text or "мама" in text:
        for key in ["маме", "мама"]:
            if key in normalized_contacts:
                return normalized_contacts[key]

    return default_chat_id


async def send_telegram_message(text, chat_id=None):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM BOT TOKEN NOT CONFIGURED", flush=True)
        return False

    if not chat_id:
        chat_id = TELEGRAM_CHAT_ID

    if not chat_id:
        print("TELEGRAM CHAT ID NOT CONFIGURED", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    if len(text) > 3900:
        text = text[:3900] + "\n\n...текст был сокращён."

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": False
                }
            )

        print(f"TELEGRAM MESSAGE STATUS: {response.status_code}", flush=True)
        print(f"TELEGRAM RESPONSE: {response.text[:500]}", flush=True)

        return response.status_code == 200

    except Exception as e:
        print(f"TELEGRAM MESSAGE ERROR: {str(e)}", flush=True)
        return False


async def send_telegram_file(filename, content, chat_id=None):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM BOT TOKEN NOT CONFIGURED", flush=True)
        return False

    if not chat_id:
        chat_id = TELEGRAM_CHAT_ID

    if not chat_id:
        print("TELEGRAM CHAT ID NOT CONFIGURED", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"

    if not filename:
        filename = "document.txt"

    if not content:
        content = "Пустой документ."

    try:
        file_bytes = io.BytesIO(content.encode("utf-8"))
        file_bytes.name = filename

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": filename
                },
                files={
                    "document": (filename, file_bytes, "text/plain")
                }
            )

        print(f"TELEGRAM FILE STATUS: {response.status_code}", flush=True)
        print(f"TELEGRAM FILE RESPONSE: {response.text[:500]}", flush=True)

        return response.status_code == 200

    except Exception as e:
        print(f"TELEGRAM FILE ERROR: {str(e)}", flush=True)
        return False


# ---------------------------------------------------------
# ПОИСК И ТОВАРЫ
# ---------------------------------------------------------

def clean_query_for_search(query):
    text = str(query or "").strip()

    replacements = [
        "и отправь в телеграм",
        "отправь в телеграм",
        "скинь в телеграм",
        "пришли в телеграм",
        "и скинь в телеграм",
        "и отправь на телефон",
        "скинь на телефон",
        "отправь на телефон",
        "найди",
        "поищи",
        "посмотри",
        "подбери",
        "выбери",
        "самый дешевый",
        "самая дешевая",
        "самые дешевые",
        "дешевый",
        "дешевая",
        "дешевые",
        "недорогой",
        "недорогая",
        "недорогие",
        "товар"
    ]

    for r in replacements:
        pattern = re.compile(re.escape(r), re.IGNORECASE)
        text = pattern.sub(" ", text)

    text = re.sub(r"\s+", " ", text).strip(" .,!?:;")

    low = normalize_text(text)

    if "логитеч" in low:
        text = re.sub("логитеч", "Logitech", text, flags=re.IGNORECASE)

    if "краснодар" not in normalize_text(text):
        if any(w in normalize_text(text) for w in [
            "шторы", "клавиатура", "мышь", "rtx", "видеокарта",
            "монитор", "ноутбук", "телефон", "смартфон", "мебель",
            "стол", "диван", "кровать"
        ]):
            text += " Краснодар"

    return text.strip()


def extract_search_query(command):
    text = command.strip()

    remove_phrases = [
        "найди в интернете",
        "поищи в интернете",
        "посмотри в интернете",
        "найди",
        "поищи",
        "посмотри",
        "подбери",
        "подобрать",
        "выбери",
        "выбрать",
        "скинь в телеграм",
        "отправь в телеграм",
        "пришли в телеграм",
        "скинь на телефон",
        "отправь на телефон",
        "сделай документ",
        "сделай файл"
    ]

    low = normalize_text(text)

    for phrase in remove_phrases:
        if low.startswith(phrase):
            words = text.split()
            phrase_len = len(phrase.split())
            text = " ".join(words[phrase_len:])
            break

    text = text.strip(" .,!?:;")

    if not text:
        text = command.strip()

    return clean_query_for_search(text)


def build_shop_queries(query):
    base = clean_query_for_search(query)

    queries = [
        f"{base} купить цена",
        f"{base} цена Краснодар",
        f"{base} купить Краснодар",
        f"{base} доставка Краснодар",

        f"{base} site:market.yandex.ru/product цена",
        f"{base} site:market.yandex.ru цена",

        f"{base} site:dns-shop.ru/product цена",
        f"{base} site:dns-shop.ru цена",

        f"{base} site:citilink.ru/product цена",
        f"{base} site:citilink.ru цена",

        f"{base} site:ozon.ru/product цена",
        f"{base} site:ozon.ru цена",

        f"{base} site:wildberries.ru/catalog цена",
        f"{base} site:wildberries.ru цена",

        f"{base} site:avito.ru/krasnodar цена",
        f"{base} Авито Краснодар цена"
    ]

    low = normalize_text(base)

    if any(w in low for w in ["шторы", "тюль", "занавески", "карниз", "ковер", "ковёр", "мебель"]):
        queries.extend([
            f"{base} site:hoff.ru цена",
            f"{base} site:lemanapro.ru цена",
            f"{base} site:leroymerlin.ru цена"
        ])

    return queries


async def web_search(query, max_results=5):
    api_key = TAVILY_API_KEY or os.getenv("TAVILY_API_KEY")
    api_url = TAVILY_API_URL or "https://api.tavily.com/search"

    if not api_key:
        print("TAVILY_API_KEY NOT CONFIGURED", flush=True)
        return {
            "ok": False,
            "error": "TAVILY_API_KEY не настроен.",
            "results": []
        }

    if not query:
        return {
            "ok": False,
            "error": "Пустой поисковый запрос.",
            "results": []
        }

    try:
        print(f"TAVILY SEARCH QUERY: {query}", flush=True)

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                api_url,
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "max_results": max_results
                }
            )

        print(f"TAVILY STATUS: {response.status_code}", flush=True)

        if response.status_code != 200:
            print(f"TAVILY ERROR BODY: {response.text[:1000]}", flush=True)
            return {
                "ok": False,
                "error": f"Ошибка Tavily: {response.status_code}",
                "results": []
            }

        data = response.json()
        raw_results = data.get("results", [])

        results = []

        for item in raw_results:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", "")
            })

        return {
            "ok": True,
            "error": "",
            "results": results
        }

    except Exception as e:
        print(f"WEB SEARCH ERROR: {str(e)}", flush=True)
        return {
            "ok": False,
            "error": str(e),
            "results": []
        }


def format_search_results(results):
    if not results:
        return "Ничего не найдено."

    text = ""

    for i, item in enumerate(results, start=1):
        title = item.get("title", "Без названия")
        url = item.get("url", "")
        content = item.get("content", "")

        text += f"{i}. {title}\n"

        if content:
            text += f"{content[:650]}\n"

        if url:
            text += f"Ссылка: {url}\n"

        text += "\n"

    return text.strip()


def extract_price_from_text(text):
    if not text:
        return None

    text = str(text)
    text = text.replace("\u202f", " ")
    text = text.replace("\xa0", " ")

    patterns = [
        r"(\d{1,3}(?:[\s\.]\d{3})+)\s*(?:₽|руб|р\b)",
        r"(\d{4,7})\s*(?:₽|руб|р\b)",
        r"от\s*(\d{1,3}(?:[\s\.]\d{3})+)",
        r"от\s*(\d{4,7})"
    ]

    prices = []

    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)

        for match in matches:
            raw = str(match)
            raw = raw.replace(" ", "").replace(".", "")

            try:
                price = int(raw)

                if 50 <= price <= 2_000_000:
                    prices.append(price)
            except Exception:
                pass

    if not prices:
        return None

    return min(prices)


def clean_product_title(title):
    title = str(title or "").strip()
    title = re.sub(r"\s+", " ", title)
    title = title.replace("— купить", "")
    title = title.replace("- купить", "")
    title = title.strip(" -—|")
    return title


def is_generic_search_url(url):
    if not url:
        return True

    url_low = url.lower()

    bad_parts = [
        "/search",
        "search?",
        "text=",
        "q=",
        "query=",
        "catalog/0/search",
        "поиск"
    ]

    return any(part in url_low for part in bad_parts)


def collect_product_candidates(results):
    candidates = []

    for item in results:
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")

        combined_text = f"{title}\n{content}"
        price = extract_price_from_text(combined_text)

        if price is None:
            continue

        if is_generic_search_url(url):
            continue

        candidates.append({
            "title": clean_product_title(title),
            "url": url,
            "price": price,
            "content": content[:500]
        })

    return candidates


def deduplicate_candidates(candidates):
    seen_urls = set()
    unique = []

    for item in candidates:
        url = item.get("url", "")

        if not url:
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)
        unique.append(item)

    return unique


def format_cheapest_products(candidates, query, limit=5):
    if not candidates:
        return ""

    candidates = deduplicate_candidates(candidates)
    candidates = sorted(candidates, key=lambda x: x.get("price", 999999999))
    candidates = candidates[:limit]

    text = f"🛒 Самые дешёвые найденные варианты:\n{query}\n\n"

    for i, item in enumerate(candidates, start=1):
        title = item.get("title", "Товар")
        price = item.get("price")
        url = item.get("url")

        price_text = f"{price:,}".replace(",", " ")

        text += f"{i}. {title}\n"
        text += f"Цена в найденных данных: {price_text} ₽\n"
        text += f"Ссылка: {url}\n\n"

    best = candidates[0]
    best_price = f"{best.get('price'):,}".replace(",", " ")

    text += "✅ Самый дешёвый из найденных вариантов:\n"
    text += f"{best.get('title')}\n"
    text += f"Цена: {best_price} ₽\n"
    text += f"{best.get('url')}\n\n"

    text += (
        "Важно: цена могла измениться. Перед покупкой проверьте цену, наличие, продавца "
        "и доставку в Краснодар."
    )

    return text


# ---------------------------------------------------------
# DEEPSEEK
# ---------------------------------------------------------

def build_messages(state, user_text, internet_context=""):
    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    facts = state.get("facts", [])
    global_facts = GLOBAL_PROFILE.get("facts", [])
    history = state.get("history", [])

    memory_text = ""

    all_facts = global_facts + facts

    if all_facts:
        memory_text += "\nЧто известно о пользователе:\n"
        for fact in all_facts[-15:]:
            memory_text += f"- {fact}\n"

    notes = GLOBAL_PROFILE.get("notes", [])
    if notes:
        memory_text += "\nЗаметки пользователя:\n"
        for note in notes[-8:]:
            memory_text += f"- {note.get('text', '')}\n"

    tasks = GLOBAL_PROFILE.get("tasks", [])
    if tasks:
        memory_text += "\nТекущие задачи пользователя:\n"
        for task in tasks[-8:]:
            memory_text += f"- {task.get('text', '')}\n"

    if internet_context:
        memory_text += "\nДанные из интернета и инструментов:\n"
        memory_text += internet_context[:9000]
        memory_text += "\n"

    system_prompt = mode["prompt"] + """

У тебя есть внешние инструменты:
- интернет-поиск;
- Telegram;
- файлы;
- карты;
- подготовка ссылок для покупки;
- заметки пользователя;
- список задач.

Правила:
1. Не выдумывай ссылки.
2. Не выдумывай точные цены.
3. Если цена не видна — скажи, что цену нужно проверить.
4. Не оформляй и не оплачивай заказы самостоятельно.
5. Для покупок учитывай Краснодар.
6. Если результат длинный — кратко для голоса, подробно в Telegram.
""" + memory_text

    messages = [{"role": "system", "content": system_prompt}]

    for item in history[-10:]:
        role = item.get("role")
        content = item.get("content")

        if role in ["user", "assistant"] and content:
            messages.append({
                "role": role,
                "content": content
            })

    messages.append({
        "role": "user",
        "content": user_text
    })

    return messages


async def ask_deepseek(user_text, state, internet_context="", long_answer=False):
    if not DEEPSEEK_API_KEY:
        return "DeepSeek API не настроен.", state

    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    messages = build_messages(state, user_text, internet_context)

    max_tokens = mode["max_tokens"]

    if long_answer:
        max_tokens = 1300

    print(f"DEEPSEEK REQUEST: {user_text}", flush=True)
    print(f"MODE: {mode_name}", flush=True)

    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            response = await client.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "temperature": mode["temperature"],
                    "max_tokens": max_tokens
                }
            )

        print(f"DEEPSEEK STATUS: {response.status_code}", flush=True)

        if response.status_code != 200:
            print(f"DEEPSEEK ERROR: {response.text[:800]}", flush=True)
            return "DeepSeek сейчас не ответил. Попробуйте позже.", state

        data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print(f"DEEPSEEK REQUEST ERROR: {str(e)}", flush=True)
        return "DeepSeek сейчас не ответил. Попробуйте позже.", state

    history = state.get("history", [])

    history.append({
        "role": "user",
        "content": user_text
    })

    history.append({
        "role": "assistant",
        "content": answer
    })

    state["history"] = history[-12:]
    state["last_result"] = answer

    return answer, state


# ---------------------------------------------------------
# НАМЕРЕНИЯ
# ---------------------------------------------------------

def wants_telegram(command):
    text = normalize_text(command)

    words = [
        "в телеграм",
        "в telegram",
        "скинь",
        "скинь на телефон",
        "отправь",
        "пришли",
        "пришли на телефон",
        "на телефон",
        "сохрани мне",
        "ссылку мне",
        "ссылки мне",
        "подбери",
        "подобрать",
        "выбери",
        "выбрать",
        "самые дешевые",
        "самый дешевый",
        "самая дешевая",
        "дешевые",
        "недорогие",
        "товар",
        "товары",
        "шторы",
        "занавески"
    ]

    return any(w in text for w in words)


def wants_file(command):
    text = normalize_text(command)

    words = [
        "файл",
        "документ",
        "таблицу",
        "таблица",
        "csv",
        "текстовым файлом",
        "отдельным файлом"
    ]

    return any(w in text for w in words)


def needs_web(command):
    text = normalize_text(command)

    words = [
        "найди",
        "поищи",
        "посмотри",
        "посмотри в интернете",
        "в интернете",
        "актуально",
        "актуальная",
        "сейчас",
        "сегодня",
        "свежие",
        "новости",
        "курс",
        "погода",
        "цена",
        "цены",
        "стоит",
        "стоимость",
        "где купить",
        "купить",
        "подбери",
        "подобрать",
        "выбери",
        "выбрать",
        "самый дешевый",
        "самые дешевые",
        "самая дешевая",
        "дешевый",
        "дешевая",
        "дешевые",
        "недорогой",
        "недорогая",
        "недорогие",
        "товар",
        "товары",
        "магазин",
        "магазины",
        "маркет",
        "маркетплейс",
        "скидки",
        "акции",
        "отзывы",
        "доставка",
        "заказать",
        "закажи",
        "еда",
        "ресторан",
        "кафе",
        "пицца",
        "суши",
        "роллы",
        "продукты",
        "адрес",
        "рядом",
        "ближайший",
        "ближайшая",
        "маршрут",
        "карта",
        "аптека",
        "шторы",
        "штора",
        "занавески",
        "тюль",
        "карниз",
        "мебель",
        "лампа",
        "светильник",
        "ковер",
        "ковёр",
        "постельное",
        "полотенца",
        "ссылка",
        "ссылки"
    ]

    return any(w in text for w in words)


def is_map_request(command):
    text = normalize_text(command)

    words = [
        "адрес",
        "рядом",
        "ближайший",
        "ближайшая",
        "карта",
        "маршрут",
        "где находится",
        "найди место",
        "найди на карте"
    ]

    return any(w in text for w in words)


def is_order_request(command):
    text = normalize_text(command)

    words = [
        "закажи",
        "заказать",
        "оформи заказ",
        "доставка",
        "привези",
        "купить",
        "товар",
        "товары",
        "магазин",
        "магазины",
        "еда",
        "пицца",
        "суши",
        "роллы",
        "бургер",
        "продукты",
        "шторы",
        "штора",
        "занавески",
        "тюль",
        "карниз",
        "ковер",
        "ковёр",
        "мебель",
        "лампа",
        "светильник",
        "постельное",
        "полотенца",
        "rtx",
        "видеокарта",
        "клавиатура",
        "мышь",
        "ноутбук",
        "телефон",
        "смартфон",
        "монитор"
    ]

    return any(w in text for w in words)


def is_phone_call_request(command):
    text = normalize_text(command)

    words = [
        "позвони",
        "набери",
        "сделай звонок",
        "позвонить"
    ]

    return any(w in text for w in words)


def shopping_needs_clarification(command):
    text = normalize_text(command)

    if any(w in text for w in ["шторы", "штора", "занавески", "тюль"]):
        has_type = any(w in text for w in [
            "блэкаут",
            "blackout",
            "тюль",
            "рулонные",
            "римские",
            "обычные",
            "портьеры",
            "занавески"
        ])

        has_size = any(w in text for w in [
            "метр",
            "метра",
            "см",
            "сантиметр",
            "ширина",
            "высота",
            "200",
            "250",
            "270",
            "300",
            "2 метра",
            "два метра",
            "два на",
            "2 на"
        ])

        has_color = any(w in text for w in [
            "белые",
            "белый",
            "серые",
            "серый",
            "бежевые",
            "бежевый",
            "черные",
            "черный",
            "чёрные",
            "чёрный",
            "зеленые",
            "зелёные",
            "синие",
            "коричневые",
            "молочные",
            "кремовые"
        ])

        if not has_type and not has_size and not has_color:
            return (
                "Какие шторы нужны: тюль, блэкаут, рулонные или обычные? "
                "И какой примерно размер?"
            )

        if has_type and not has_size:
            return "Какой примерно размер штор: ширина и высота?"

    if any(w in text for w in ["диван", "кровать", "стол", "шкаф", "комод"]):
        has_size = any(w in text for w in ["размер", "ширина", "высота", "длина", "см", "метр"])
        has_budget = any(w in text for w in ["до ", "руб", "тысяч", "бюджет"])

        if not has_size and not has_budget:
            return "Уточните размер и примерный бюджет."

    if any(w in text for w in ["куртку", "куртка", "джинсы", "футболку", "обувь", "кроссовки"]):
        has_size = any(w in text for w in ["размер", "s", "m", "l", "xl", "42", "43", "44", "45"])

        if not has_size:
            return "Какой размер нужен?"

    return None


# ---------------------------------------------------------
# КАРТЫ, ТЕЛЕФОН, КОНТАКТЫ
# ---------------------------------------------------------

def make_map_links(query):
    encoded = urllib.parse.quote(query)

    yandex = f"https://yandex.ru/maps/?text={encoded}"
    google = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    return yandex, google


def extract_phone_number(text):
    match = re.search(r"(\+?\d[\d\-\s\(\)]{7,}\d)", text)

    if not match:
        return None

    number = match.group(1)
    number = re.sub(r"[^\d\+]", "", number)

    if number.startswith("8") and len(number) == 11:
        number = "+7" + number[1:]

    if not number.startswith("+") and len(number) >= 10:
        number = "+" + number

    return number


def save_contact_from_command(command):
    text = command.strip()
    low = normalize_text(text)

    if not ("телефон" in low or "номер" in low):
        return None

    number = extract_phone_number(text)

    if not number:
        return None

    cleaned = low
    cleaned = cleaned.replace("запомни телефон", "")
    cleaned = cleaned.replace("запомни номер", "")
    cleaned = cleaned.replace("телефон", "")
    cleaned = cleaned.replace("номер", "")
    cleaned = re.sub(r"\+?\d[\d\-\s\(\)]{7,}\d", "", cleaned)
    cleaned = re.sub(r"\d+", "", cleaned)

    name = cleaned.strip()

    if not name:
        name = "контакт"

    GLOBAL_PROFILE["contacts"][name] = number

    return name, number


def save_telegram_contact_from_command(command):
    text = command.strip()
    low = normalize_text(text)

    if not ("телеграм" in low or "telegram" in low):
        return None

    match = re.search(r"(-?\d{5,})", text)

    if not match:
        return None

    chat_id = match.group(1)

    cleaned = low
    cleaned = cleaned.replace("запомни телеграм", "")
    cleaned = cleaned.replace("запомни telegram", "")
    cleaned = cleaned.replace("телеграм", "")
    cleaned = cleaned.replace("telegram", "")
    cleaned = cleaned.replace(chat_id, "")

    name = cleaned.strip()

    if not name:
        name = "контакт"

    GLOBAL_PROFILE["telegram_contacts"][name] = chat_id

    return name, chat_id


async def handle_call_request(command):
    text = normalize_text(command)

    number = extract_phone_number(command)
    contact_name = None

    if not number:
        contacts = GLOBAL_PROFILE.get("contacts", {})

        for name, saved_number in contacts.items():
            if name in text:
                contact_name = name
                number = saved_number
                break

    if not number:
        contacts_text = ""

        contacts = GLOBAL_PROFILE.get("contacts", {})
        if contacts:
            contacts_text = "\n\nСохранённые контакты:\n"
            for name, num in contacts.items():
                contacts_text += f"- {name}: {num}\n"

        await send_telegram_message(
            "Я не могу сама звонить через колонку.\n"
            "Но могу отправить ссылку для звонка, если вы сохраните номер.\n\n"
            "Пример команды:\n"
            "запомни телефон мамы +79991234567"
            + contacts_text
        )

        return "Я не могу звонить сама. Отправила подсказку в Телеграм."

    label = contact_name or "номер"

    msg = (
        f"☎️ Звонок: {label}\n\n"
        f"Номер: {number}\n"
        f"Нажмите на ссылку с телефона:\n"
        f"tel:{number}\n\n"
        f"Важно: навык Алисы не может сам совершить звонок. "
        f"Финально звонок запускаете вы на телефоне."
    )

    await send_telegram_message(msg)

    return "Я не могу позвонить сама, но отправила ссылку для звонка в Телеграм."


# ---------------------------------------------------------
# ПАМЯТЬ, ЗАМЕТКИ, ЗАДАЧИ
# ---------------------------------------------------------

def extract_fact(command):
    text = command.strip()

    patterns = [
        "запомни, что",
        "запомни что",
        "запомни"
    ]

    low = normalize_text(text)

    for p in patterns:
        if low.startswith(p):
            words = text.split()
            count = len(p.split())
            fact = " ".join(words[count:])
            return fact.strip(" .,!?:;")

    return text.strip(" .,!?:;")


def try_auto_remember_name(command, state):
    text = command.strip()
    low = normalize_text(text)

    name = None

    if low.startswith("меня зовут "):
        name = text.split(" ", 2)[-1].strip(" .,!?:;")

    if low.startswith("мое имя "):
        name = text.split(" ", 2)[-1].strip(" .,!?:;")

    if name:
        fact = f"пользователя зовут {name}"

        facts = state.get("facts", [])
        facts = [f for f in facts if "пользователя зовут" not in f.lower()]
        facts.append(fact)

        state["facts"] = facts[-20:]

        GLOBAL_PROFILE["facts"] = [
            f for f in GLOBAL_PROFILE["facts"]
            if "пользователя зовут" not in f.lower()
        ]
        GLOBAL_PROFILE["facts"].append(fact)

        return name

    return None


def add_to_shopping_list(command, state):
    text = command.strip()
    low = normalize_text(text)

    triggers = [
        "добавь в список покупок",
        "добавь в покупки",
        "в список покупок"
    ]

    item_text = None

    for t in triggers:
        if low.startswith(t):
            words = text.split()
            item_text = " ".join(words[len(t.split()):])
            break

    if not item_text:
        return None

    parts = re.split(r",| и ", item_text)
    items = [p.strip(" .,!?:;") for p in parts if p.strip(" .,!?:;")]

    shopping_list = state.get("shopping_list", [])
    shopping_list.extend(items)
    state["shopping_list"] = shopping_list[-100:]

    return items


def save_note(command):
    text = command.strip()
    low = normalize_text(text)

    prefixes = [
        "сохрани заметку",
        "запиши заметку",
        "заметка",
        "запиши"
    ]

    note = text

    for p in prefixes:
        if low.startswith(p):
            words = text.split()
            note = " ".join(words[len(p.split()):])
            break

    note = note.strip(" .,!?:;")

    if not note:
        return None

    item = {
        "text": note,
        "created_at": time.time()
    }

    GLOBAL_PROFILE["notes"].append(item)
    GLOBAL_PROFILE["notes"] = GLOBAL_PROFILE["notes"][-100:]

    return note


def search_notes(query):
    q = normalize_text(query)
    results = []

    for note in GLOBAL_PROFILE.get("notes", []):
        text = note.get("text", "")
        if q in normalize_text(text):
            results.append(text)

    return results[-10:]


def add_task(command):
    text = command.strip()
    low = normalize_text(text)

    prefixes = [
        "добавь задачу",
        "создай задачу",
        "задача",
        "запиши задачу"
    ]

    task = text

    for p in prefixes:
        if low.startswith(p):
            words = text.split()
            task = " ".join(words[len(p.split()):])
            break

    task = task.strip(" .,!?:;")

    if not task:
        return None

    item = {
        "text": task,
        "created_at": time.time(),
        "done": False
    }

    GLOBAL_PROFILE["tasks"].append(item)
    GLOBAL_PROFILE["tasks"] = GLOBAL_PROFILE["tasks"][-100:]

    return task


# ---------------------------------------------------------
# ФОНОВЫЙ АГЕНТ
# ---------------------------------------------------------

async def background_agent_task(command, session_id, state_snapshot):
    try:
        state = copy.deepcopy(state_snapshot)
        query = extract_search_query(command)

        recipient_chat_id = get_telegram_recipient_chat_id(command)

        internet_context = ""
        links = []
        product_candidates = []

        if is_map_request(command):
            yandex, google = make_map_links(query)

            map_text = (
                f"🗺 Карты по запросу:\n\n"
                f"{query}\n\n"
                f"Яндекс.Карты:\n{yandex}\n\n"
                f"Google Maps:\n{google}\n"
            )

            links.extend([yandex, google])
            internet_context += map_text + "\n\n"

        if is_order_request(command):
            shop_queries = build_shop_queries(query)

            internet_context += (
                "Задача: найти конкретные товары с ценами, желательно доступные в Краснодаре "
                "или с доставкой в Краснодар. Не использовать обычные ссылки на страницы поиска как результат.\n\n"
            )

            for shop_query in shop_queries:
                search = await web_search(shop_query, max_results=5)

                internet_context += f"\nПоиск: {shop_query}\n"

                if search["ok"] and search["results"]:
                    results_text = format_search_results(search["results"])
                    internet_context += results_text + "\n"

                    candidates = collect_product_candidates(search["results"])
                    product_candidates.extend(candidates)

                    for item in search["results"]:
                        if item.get("url"):
                            links.append(item["url"])
                else:
                    internet_context += "Ничего полезного не найдено.\n"

            product_candidates = deduplicate_candidates(product_candidates)
            product_candidates = sorted(
                product_candidates,
                key=lambda x: x.get("price", 999999999)
            )

            if product_candidates:
                cheapest_text = format_cheapest_products(
                    product_candidates,
                    query,
                    limit=5
                )

                state["last_result"] = cheapest_text
                state["last_links"] = [x["url"] for x in product_candidates[:10] if x.get("url")]

                await send_telegram_message(cheapest_text, chat_id=recipient_chat_id)
                save_state(session_id, state)
                return

            no_price_text = (
                f"Я поискала варианты по запросу:\n{query}\n\n"
                f"Но не смогла достоверно вытащить цены из найденных страниц. "
                f"Поэтому не буду выдавать обычные ссылки на поиск как будто это подборка.\n\n"
                f"Это ограничение обычного веб-поиска: маркетплейсы часто скрывают цены или показывают их через приложение.\n\n"
                f"Что можно сделать:\n"
                f"1. Уточнить модель, размер, цвет или бюджет.\n"
                f"2. Попросить искать на конкретной площадке: Авито, DNS, Ситилинк, Яндекс Маркет.\n"
                f"3. Попросить: только новые товары или можно б/у.\n\n"
                f"Пример:\n"
                f"найди самую дешёвую клавиатуру Logitech K380 в Краснодаре\n"
            )

            state["last_result"] = no_price_text
            await send_telegram_message(no_price_text, chat_id=recipient_chat_id)
            save_state(session_id, state)
            return

        elif needs_web(command):
            search = await web_search(query, max_results=6)

            if search["ok"] and search["results"]:
                results_text = format_search_results(search["results"])
                internet_context += "\nРезультаты интернет-поиска:\n" + results_text

                for item in search["results"]:
                    if item.get("url"):
                        links.append(item["url"])
            else:
                internet_context += f"\nИнтернет-поиск не сработал или ничего не нашёл: {search['error']}"

        prompt = (
            f"Задача пользователя: {command}\n\n"
            f"Подготовь полезный результат для Telegram.\n\n"
            f"Правила:\n"
            f"1. Не выдавай обычные ссылки на поиск как готовую подборку.\n"
            f"2. Не выдумывай магазины, цены и ссылки.\n"
            f"3. Учитывай Краснодар.\n"
            f"4. Если данных мало — честно скажи.\n"
            f"5. В конце дай короткий практический вывод.\n"
        )

        answer, state = await ask_deepseek(
            prompt,
            state,
            internet_context=internet_context,
            long_answer=True
        )

        unique_links = []
        for link in links:
            if link and link not in unique_links:
                unique_links.append(link)

        if unique_links and not is_order_request(command):
            state["last_links"] = unique_links[-30:]

            links_text = "\n\n🔗 Ссылки:\n"
            for i, link in enumerate(unique_links[:10], start=1):
                links_text += f"{i}. {link}\n"

            if "http" not in answer:
                answer += links_text

        state["last_result"] = answer

        if wants_file(command):
            filename = "assistant_result.txt"

            low = normalize_text(command)

            if "таблиц" in low:
                filename = "table.txt"
            elif "рецепт" in low:
                filename = "recipe.txt"
            elif "документ" in low:
                filename = "document.txt"

            await send_telegram_file(filename, answer, chat_id=recipient_chat_id)
            await send_telegram_message("Готово. Файл отправлен выше.", chat_id=recipient_chat_id)
        else:
            await send_telegram_message(answer, chat_id=recipient_chat_id)

        save_state(session_id, state)

    except Exception as e:
        print(f"BACKGROUND TASK ERROR: {str(e)}", flush=True)
        await send_telegram_message(
            f"Произошла ошибка при выполнении задачи:\n{str(e)}"
        )


# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------

@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "DeepSeek Alice agent is running",
        "greeting": "Привет!",
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "internet_configured": bool(TAVILY_API_KEY),
        "telegram_contacts": list(GLOBAL_PROFILE.get("telegram_contacts", {}).keys()),
        "notes_count": len(GLOBAL_PROFILE.get("notes", [])),
        "tasks_count": len(GLOBAL_PROFILE.get("tasks", [])),
        "memory": "server session memory",
        "active_sessions": len(SERVER_SESSIONS),
        "features": [
            "DeepSeek",
            "internet search",
            "Telegram messages",
            "Telegram files",
            "maps links",
            "safe order preparation",
            "shopping clarification through voice",
            "cheapest product extraction",
            "notes",
            "tasks",
            "board of directors mode",
            "Krasnodar shopping context",
            "session memory",
            "modes",
            "phone call link helper"
        ]
    }


@app.post("/")
async def alice_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(make_response("Ошибка чтения запроса."))

    session = data.get("session", {})
    session_id = session.get("session_id", "")
    is_new = session.get("new", False)

    req = data.get("request", {})
    command = req.get("command", "").strip()
    text = normalize_text(command)

    state = get_state(session_id, is_new=is_new)

    print("YANDEX REQUEST RECEIVED", flush=True)
    print(f"SESSION ID: {session_id}", flush=True)
    print(f"IS NEW: {is_new}", flush=True)
    print(f"COMMAND: {command}", flush=True)
    print(f"STATE: {state}", flush=True)

    if is_new or not command:
        save_state(session_id, state)
        return JSONResponse(make_response("Привет!"))

    if text in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        save_state(session_id, state)
        return JSONResponse(make_response("Хорошо.", end_session=True))

    if text in ["помощь", "что ты умеешь", "команды"]:
        save_state(session_id, state)
        return JSONResponse(make_response(help_text()))

    pending_task = state.get("pending_task")

    if pending_task and pending_task.get("type") == "shopping_clarification":
        original_command = pending_task.get("original_command", "")

        combined_command = (
            original_command
            + ". Уточнение пользователя: "
            + command
            + ". Найди реальные варианты в магазинах с доставкой или наличием в Краснодаре. Отправь результат в Телеграм."
        )

        state["pending_task"] = None
        state_snapshot = copy.deepcopy(state)

        background_tasks.add_task(
            background_agent_task,
            combined_command,
            session_id,
            state_snapshot
        )

        save_state(session_id, state)

        return JSONResponse(make_response("Поняла. Ищу варианты в магазинах и отправлю в Телеграм."))

    if text in ["какой режим", "какой сейчас режим", "текущий режим"]:
        mode_name = state.get("mode", "обычный")
        title = MODES.get(mode_name, MODES["обычный"])["title"]
        save_state(session_id, state)
        return JSONResponse(make_response(f"Сейчас включён: {title}."))

    detected_mode = detect_mode_command(command)

    if detected_mode:
        state["mode"] = detected_mode
        save_state(session_id, state)

        title = MODES[detected_mode]["title"]

        if detected_mode == "джарвис":
            return JSONResponse(make_response("Режим Джарвис активирован."))
        if detected_mode == "оператор":
            return JSONResponse(make_response("Режим оператора включён."))
        if detected_mode == "исследователь":
            return JSONResponse(make_response("Режим исследователя включён."))
        if detected_mode == "совет":
            return JSONResponse(make_response("Совет директоров собран."))

        return JSONResponse(make_response(f"{title} включён."))

    # Telegram-контакт
    if text.startswith("запомни телеграм") or text.startswith("запомни telegram"):
        result = save_telegram_contact_from_command(command)

        if result:
            name, chat_id = result
            save_state(session_id, state)
            return JSONResponse(make_response(f"Запомнила Telegram для контакта: {name}."))

        save_state(session_id, state)
        return JSONResponse(make_response("Не смогла распознать Telegram chat id."))

    # Телефонный контакт
    if text.startswith("запомни телефон") or text.startswith("запомни номер"):
        result = save_contact_from_command(command)

        if result:
            name, number = result
            save_state(session_id, state)
            return JSONResponse(make_response(f"Запомнила телефон: {name}."))

        save_state(session_id, state)
        return JSONResponse(make_response("Не смогла распознать номер."))

    # Звонок
    if is_phone_call_request(command):
        answer = await handle_call_request(command)
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

    # Заметки
    if text.startswith("сохрани заметку") or text.startswith("запиши заметку") or text.startswith("заметка "):
        note = save_note(command)

        if note:
            save_state(session_id, state)
            return JSONResponse(make_response("Заметку сохранила."))

        save_state(session_id, state)
        return JSONResponse(make_response("Что записать в заметку?"))

    if text in ["покажи заметки", "мои заметки", "список заметок"]:
        notes = GLOBAL_PROFILE.get("notes", [])

        if not notes:
            save_state(session_id, state)
            return JSONResponse(make_response("Заметок пока нет."))

        msg = "📝 Заметки:\n\n"
        for i, note in enumerate(notes[-10:], start=1):
            msg += f"{i}. {note.get('text', '')}\n"

        await send_telegram_message(msg)
        save_state(session_id, state)
        return JSONResponse(make_response("Отправила заметки в Телеграм."))

    if text.startswith("найди в заметках"):
        query = command.split(" ", 3)[-1] if len(command.split()) > 3 else ""
        results = search_notes(query)

        if not results:
            save_state(session_id, state)
            return JSONResponse(make_response("В заметках ничего не нашла."))

        msg = "🔎 Нашла в заметках:\n\n"
        for i, item in enumerate(results, start=1):
            msg += f"{i}. {item}\n"

        await send_telegram_message(msg)
        save_state(session_id, state)
        return JSONResponse(make_response("Нашла и отправила в Телеграм."))

    # Задачи
    if text.startswith("добавь задачу") or text.startswith("создай задачу") or text.startswith("задача "):
        task = add_task(command)

        if task:
            save_state(session_id, state)
            return JSONResponse(make_response("Задачу добавила."))

        save_state(session_id, state)
        return JSONResponse(make_response("Какую задачу добавить?"))

    if text in ["покажи задачи", "мои задачи", "список задач"]:
        tasks = GLOBAL_PROFILE.get("tasks", [])

        if not tasks:
            save_state(session_id, state)
            return JSONResponse(make_response("Задач пока нет."))

        msg = "✅ Задачи:\n\n"
        for i, task in enumerate(tasks[-15:], start=1):
            msg += f"{i}. {task.get('text', '')}\n"

        await send_telegram_message(msg)
        save_state(session_id, state)
        return JSONResponse(make_response("Отправила задачи в Телеграм."))

    if text in ["очисти задачи", "удали задачи", "очисти список задач"]:
        GLOBAL_PROFILE["tasks"] = []
        save_state(session_id, state)
        return JSONResponse(make_response("Задачи очищены."))

    # Запомнить факт
    if text.startswith("запомни"):
        fact = extract_fact(command)

        if not fact:
            save_state(session_id, state)
            return JSONResponse(make_response("Что именно запомнить?"))

        state["facts"].append(fact)
        state["facts"] = state["facts"][-20:]

        GLOBAL_PROFILE["facts"].append(fact)
        GLOBAL_PROFILE["facts"] = GLOBAL_PROFILE["facts"][-50:]

        save_state(session_id, state)
        return JSONResponse(make_response(f"Запомнила: {fact}."))

    remembered_name = try_auto_remember_name(command, state)

    if remembered_name:
        save_state(session_id, state)
        return JSONResponse(make_response(f"Приятно познакомиться, {remembered_name}."))

    if text in [
        "что ты помнишь",
        "что ты обо мне помнишь",
        "что ты знаешь обо мне"
    ]:
        all_facts = GLOBAL_PROFILE.get("facts", []) + state.get("facts", [])
        contacts = GLOBAL_PROFILE.get("contacts", {})
        telegram_contacts = GLOBAL_PROFILE.get("telegram_contacts", {})

        if not all_facts and not contacts and not telegram_contacts:
            save_state(session_id, state)
            return JSONResponse(make_response("Пока я ничего не запомнила."))

        result = ""

        if all_facts:
            result += "Я помню: " + "; ".join(all_facts[-10:]) + ". "

        if contacts:
            result += "Телефонные контакты: " + ", ".join(contacts.keys()) + ". "

        if telegram_contacts:
            result += "Telegram-контакты: " + ", ".join(telegram_contacts.keys()) + "."

        save_state(session_id, state)
        return JSONResponse(make_response(result))

    if text in ["очисти память", "сотри память", "забудь все", "забудь всё"]:
        state["facts"] = []
        state["history"] = []
        state["last_result"] = ""
        state["last_links"] = []
        state["shopping_list"] = []
        state["pending_task"] = None

        save_state(session_id, state)
        return JSONResponse(make_response("Память текущего диалога очищена."))

    # Список покупок
    added_items = add_to_shopping_list(command, state)

    if added_items:
        save_state(session_id, state)
        return JSONResponse(make_response("Добавила в список покупок."))

    if text in ["покажи список покупок", "что в списке покупок", "список покупок"]:
        items = state.get("shopping_list", [])

        if not items:
            save_state(session_id, state)
            return JSONResponse(make_response("Список покупок пуст."))

        result = "Список покупок: " + ", ".join(items)
        save_state(session_id, state)
        return JSONResponse(make_response(result))

    if text in ["очисти список покупок", "удали список покупок"]:
        state["shopping_list"] = []
        save_state(session_id, state)
        return JSONResponse(make_response("Список покупок очищен."))

    if "список покупок" in text and wants_telegram(command):
        items = state.get("shopping_list", [])

        if not items:
            save_state(session_id, state)
            return JSONResponse(make_response("Список покупок пуст."))

        message = "🛒 Список покупок:\n\n"
        for item in items:
            message += f"- {item}\n"

        recipient_chat_id = get_telegram_recipient_chat_id(command)
        ok = await send_telegram_message(message, chat_id=recipient_chat_id)

        save_state(session_id, state)

        if ok:
            return JSONResponse(make_response("Отправила список покупок."))
        else:
            return JSONResponse(make_response("Не получилось отправить в Телеграм."))

    # Скинуть последнее
    if text in [
        "скинь на телефон",
        "отправь в телеграм",
        "скинь в телеграм",
        "пришли в телеграм",
        "отправь последнее",
        "скинь последнее"
    ]:
        last = state.get("last_result", "")

        if not last:
            save_state(session_id, state)
            return JSONResponse(make_response("Пока нечего отправлять."))

        recipient_chat_id = get_telegram_recipient_chat_id(command)
        ok = await send_telegram_message(last, chat_id=recipient_chat_id)

        save_state(session_id, state)

        if ok:
            return JSONResponse(make_response("Отправила в Телеграм."))
        else:
            return JSONResponse(make_response("Телеграм не настроен или не ответил."))

    # Уточнение для покупок
    clarification = shopping_needs_clarification(command)

    if clarification:
        state["pending_task"] = {
            "type": "shopping_clarification",
            "original_command": command
        }

        save_state(session_id, state)
        return JSONResponse(make_response(clarification))

    # Фоновые задачи: товары, Telegram, файлы
    if wants_telegram(command) or wants_file(command) or is_order_request(command):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            save_state(session_id, state)
            return JSONResponse(make_response("Телеграм пока не настроен."))

        state_snapshot = copy.deepcopy(state)

        background_tasks.add_task(
            background_agent_task,
            command,
            session_id,
            state_snapshot
        )

        save_state(session_id, state)

        if wants_file(command):
            return JSONResponse(make_response("Подготовлю файл и отправлю в Телеграм."))

        if is_order_request(command):
            return JSONResponse(make_response("Ищу варианты и отправлю в Телеграм."))

        return JSONResponse(make_response("Сделаю и отправлю в Телеграм."))

    # Интернет без Telegram
    if needs_web(command):
        query = extract_search_query(command)
        internet_context = ""

        if is_map_request(command):
            yandex, google = make_map_links(query)
            internet_context += (
                f"Ссылки на карты:\n"
                f"Яндекс.Карты: {yandex}\n"
                f"Google Maps: {google}\n\n"
            )

        search = await web_search(query, max_results=4)

        if search["ok"]:
            results_text = format_search_results(search["results"])
            internet_context += "\nРезультаты поиска:\n" + results_text

            state["last_links"] = [
                item["url"] for item in search["results"] if item.get("url")
            ][-20:]
        else:
            internet_context += f"\nПоиск не сработал: {search['error']}"

        answer, state = await ask_deepseek(
            command,
            state,
            internet_context=internet_context,
            long_answer=False
        )

        state["last_result"] = answer
        save_state(session_id, state)

        return JSONResponse(make_response(answer))

    # Обычный DeepSeek
    try:
        answer, state = await ask_deepseek(command, state)
        state["last_result"] = answer
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

    except Exception as e:
        print(f"SERVER ERROR: {str(e)}", flush=True)
        save_state(session_id, state)
        return JSONResponse(make_response("Я не успела получить ответ. Попробуйте короче."))
