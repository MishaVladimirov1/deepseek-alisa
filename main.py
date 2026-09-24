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

# Необязательно.
# Потом можно добавить в Render переменную:
# TELEGRAM_CONTACTS_JSON={"жене":"123456789","маме":"987654321"}
TELEGRAM_CONTACTS_JSON = os.getenv("TELEGRAM_CONTACTS_JSON", "{}")

try:
    TELEGRAM_CONTACTS = json.loads(TELEGRAM_CONTACTS_JSON)
    if not isinstance(TELEGRAM_CONTACTS, dict):
        TELEGRAM_CONTACTS = {}
except Exception:
    TELEGRAM_CONTACTS = {}


# Память текущих сессий навыка.
SERVER_SESSIONS = {}

# Простая глобальная память.
# Важно: на бесплатном Render она может сбрасываться после перезапуска сервера.
GLOBAL_PROFILE = {
    "facts": [
        "пользователь находится в Краснодаре",
        "для покупок учитывать доставку или наличие в Краснодаре"
    ],
    "contacts": {},
    "telegram_contacts": TELEGRAM_CONTACTS
}


MODES = {
    "обычный": {
        "title": "Обычный режим",
        "prompt": """
Ты голосовой AI-ассистент пользователя внутри навыка Алисы.
Отвечай по-русски, понятно, дружелюбно и не слишком длинно.
Если вопрос простой — отвечай сам.
Если в контексте есть данные из интернета — используй их.
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
Обращайся уважительно.
Отвечай по-русски.
Если пользователь просит сделать действие — помогай выполнить его через доступные инструменты.
""",
        "max_tokens": 550,
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
        "max_tokens": 750,
        "temperature": 0.5
    },

    "оператор": {
        "title": "Режим оператора",
        "prompt": """
Ты режим оператора.
Говори очень кратко.
Если задача выполнена, отвечай: сделано, отправила, нашла, подготовила.
Подробности лучше отправлять в Telegram, если это возможно.
""",
        "max_tokens": 250,
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
        "max_tokens": 700,
        "temperature": 0.6
    },

    "покупки": {
        "title": "Режим покупок",
        "prompt": """
Ты помощник по покупкам.
Помогай искать товары, сравнивать варианты, проверять риски, составлять список.
Учитывай город Краснодар и доставку в Краснодар.
Не оформляй и не оплачивай заказы самостоятельно.
Всегда предлагай пользователю самому подтвердить покупку.
Не выдумывай цены и ссылки.
""",
        "max_tokens": 750,
        "temperature": 0.5
    },

    "дом": {
        "title": "Режим дом",
        "prompt": """
Ты домашний помощник.
Помогай с рецептами, бытовыми задачами, ремонтом, покупками, списками дел и инструкциями.
Отвечай просто и практично.
""",
        "max_tokens": 650,
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
        "max_tokens": 550,
        "temperature": 0.6
    },

    "учитель": {
        "title": "Режим учителя",
        "prompt": """
Ты добрый учитель.
Объясняй простыми словами, с примерами.
Если тема сложная — разбивай на шаги.
""",
        "max_tokens": 650,
        "temperature": 0.6
    }
}


# ---------------------------------------------------------
# Базовые функции
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

    # Для колонки лучше коротко.
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
# Режимы
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
        "преподаватель": "учитель"
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
режим дом.

Можно сказать:
найди в интернете ...
подбери самые дешёвые ...
скинь в телеграм
сделай документ
сделай таблицу
найди рядом
запомни, что ...
что ты помнишь
добавь в список покупок ...
покажи список покупок
запомни телефон мамы +79991234567
позвони маме
"""


# ---------------------------------------------------------
# Telegram
# ---------------------------------------------------------

def get_telegram_recipient_chat_id(command=None):
    """
    По умолчанию отправляем владельцу.
    Если в команде есть 'жене', 'маме' и т.п.,
    пробуем найти chat_id в TELEGRAM_CONTACTS_JSON или GLOBAL_PROFILE.
    """

    default_chat_id = TELEGRAM_CHAT_ID

    if not command:
        return default_chat_id

    text = normalize_text(command)

    contacts = {}
    contacts.update(GLOBAL_PROFILE.get("telegram_contacts", {}))

    # Нормализуем ключи.
    normalized_contacts = {}
    for name, chat_id in contacts.items():
        normalized_contacts[normalize_text(name)] = str(chat_id)

    for name, chat_id in normalized_contacts.items():
        if name and name in text:
            return chat_id

    # Частые варианты.
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
# Интернет-поиск
# ---------------------------------------------------------

def build_shop_queries(query):
    """
    Делаем запросы так, чтобы найти именно товары с ценами,
    а не просто страницы поиска.
    """

    base = query

    queries = [
        f"{base} купить цена",
        f"{base} цена Краснодар",
        f"{base} купить Краснодар",
        f"{base} доставка Краснодар",

        f"{base} site:market.yandex.ru/product",
        f"{base} site:market.yandex.ru цена",

        f"{base} site:dns-shop.ru/product",
        f"{base} site:dns-shop.ru цена",

        f"{base} site:citilink.ru/product",
        f"{base} site:citilink.ru цена",

        f"{base} site:ozon.ru/product",
        f"{base} site:ozon.ru цена",

        f"{base} site:wildberries.ru/catalog",
        f"{base} site:wildberries.ru цена",

        f"{base} site:avito.ru/krasnodar",
        f"{base} Авито Краснодар цена"
    ]

    # Для дома/мебели добавляем магазины дома
    low = normalize_text(base)

    if any(w in low for w in ["шторы", "тюль", "занавески", "карниз", "ковер", "мебель"]):
        queries.extend([
            f"{base} site:hoff.ru цена",
            f"{base} site:lemanapro.ru цена",
            f"{base} site:leroymerlin.ru цена"
        ])

    return queries
def extract_price_from_text(text):
    """
    Пытается найти цену в тексте.
    Возвращает число в рублях или None.
    """

    if not text:
        return None

    text = str(text)
    text = text.replace("\u202f", " ")
    text = text.replace("\xa0", " ")

    patterns = [
        r"(\d{1,3}(?:[\s\.]\d{3})+)\s*(?:₽|руб|р)",
        r"(\d{4,7})\s*(?:₽|руб|р)",
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

                # Отсекаем мусорные числа
                if 50 <= price <= 2_000_000:
                    prices.append(price)
            except Exception:
                pass

    if not prices:
        return None

    return min(prices)


def clean_product_title(title):
    title = str(title or "").strip()

    # Убираем мусор
    title = re.sub(r"\s+", " ", title)
    title = title.replace("— купить", "")
    title = title.replace("- купить", "")
    title = title.strip(" -—|")

    return title


def is_generic_search_url(url):
    """
    Проверяет, является ли ссылка просто страницей поиска.
    Такие ссылки нельзя выдавать как найденный товар.
    """

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
    """
    Превращает результаты поиска в список кандидатов:
    title, url, price, snippet.
    Берём только то, где есть цена и нормальная ссылка.
    """

    candidates = []

    for item in results:
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")

        combined_text = f"{title}\n{content}"

        price = extract_price_from_text(combined_text)

        # Если цены нет — это не кандидат на "самый дешёвый"
        if price is None:
            continue

        # Если это просто ссылка на поиск — не считаем товаром
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
    """
    Убирает дубликаты по ссылке и похожему названию.
    """

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
    """
    Формирует нормальное сообщение с самыми дешёвыми вариантами.
    """

    if not candidates:
        return ""

    candidates = deduplicate_candidates(candidates)
    candidates = sorted(candidates, key=lambda x: x.get("price", 999999999))
    candidates = candidates[:limit]

    text = f"🛒 Самые дешёвые найденные варианты по запросу:\n{query}\n\n"

    for i, item in enumerate(candidates, start=1):
        title = item.get("title", "Товар")
        price = item.get("price")
        url = item.get("url")

        text += f"{i}. {title}\n"
        text += f"Цена в найденных данных: {price:,} ₽\n".replace(",", " ")
        text += f"Ссылка: {url}\n\n"

    best = candidates[0]

    text += (
        f"✅ Самый дешёвый из найденных вариантов:\n"
        f"{best.get('title')}\n"
        f"Цена: {best.get('price'):,} ₽\n"
        f"{best.get('url')}\n\n"
    ).replace(",", " ")

    text += (
        "Важно: цена могла измениться. Перед покупкой проверьте цену, наличие, продавца и доставку в Краснодар."
    )

    return text
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


# ---------------------------------------------------------
# DeepSeek
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

    if internet_context:
        memory_text += "\nДанные из интернета и инструментов:\n"
        memory_text += internet_context[:9000]
        memory_text += "\n"

    system_prompt = mode["prompt"] + """

У тебя есть внешние инструменты, которые выполняет сервер:
- интернет-поиск;
- отправка сообщений в Telegram;
- отправка файлов;
- создание ссылок на карты;
- подготовка ссылок для заказа товаров и еды.

Важные правила:
1. Не выдумывай ссылки. Используй только ссылки из поиска или явно созданные ссылки на карты/магазины.
2. Не выдумывай точные цены, если их нет в найденных данных.
3. Если цена не видна — напиши: цену нужно проверить по ссылке.
4. Если пользователь просит отправить что-то на телефон, результат должен быть пригоден для Telegram.
5. Не оформляй и не оплачивай заказы самостоятельно.
6. Для покупок, еды и доставки готовь варианты и ссылки, но финальное подтверждение делает пользователь.
7. Если данных мало, честно скажи об этом.
8. Для голосового ответа отвечай коротко, а подробности лучше отправлять в Telegram.
9. Для покупок учитывай Краснодар и доставку в Краснодар.
""" + memory_text

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

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
# Определение намерений
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

        # Чтобы подборки товаров чаще уходили в Telegram.
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
        # общее
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

        # покупки
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

        # доставка/еда
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

        # места
        "адрес",
        "рядом",
        "ближайший",
        "ближайшая",
        "маршрут",
        "карта",
        "аптека",

        # товары для дома
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

        # отправка ссылок
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

        # бытовые покупки
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
        "полотенца"
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
    """
    Проверяет, нужно ли задать уточняющий вопрос по покупке.
    Например, для штор желательно знать тип, размер, цвет.
    """
    text = normalize_text(command)

    # Шторы
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

        # Если вообще мало данных — спрашиваем.
        if not has_type and not has_size and not has_color:
            return (
                "Какие шторы нужны: тюль, блэкаут, рулонные или обычные? "
                "И какой примерно размер?"
            )

        # Если есть тип, но нет размера.
        if has_type and not has_size:
            return "Какой примерно размер штор: ширина и высота?"

    # Мебель
    if any(w in text for w in ["диван", "кровать", "стол", "шкаф", "комод"]):
        has_size = any(w in text for w in ["размер", "ширина", "высота", "длина", "см", "метр"])
        has_budget = any(w in text for w in ["до ", "руб", "тысяч", "бюджет"])

        if not has_size and not has_budget:
            return "Уточните размер и примерный бюджет."

    # Одежда/обувь
    if any(w in text for w in ["куртку", "куртка", "джинсы", "футболку", "обувь", "кроссовки"]):
        has_size = any(w in text for w in ["размер", "s", "m", "l", "xl", "42", "43", "44", "45"])

        if not has_size:
            return "Какой размер нужен?"

    return None


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

    # По умолчанию добавляем Краснодар, если это покупка/место/доставка.
    low2 = normalize_text(text)

    shopping_words = [
        "шторы",
        "штора",
        "занавески",
        "тюль",
        "товар",
        "купить",
        "магазин",
        "доставка",
        "цена",
        "дешевые",
        "недорогие",
        "мебель",
        "еда",
        "пицца",
        "суши",
        "продукты",
        "ковер",
        "ковёр",
        "лампа",
        "светильник"
    ]

    if any(w in low2 for w in shopping_words):
        if "краснодар" not in low2:
            text = text + " Краснодар"

    return text


# ---------------------------------------------------------
# Карты, заказы, телефон
# ---------------------------------------------------------

def make_map_links(query):
    encoded = urllib.parse.quote(query)

    yandex = f"https://yandex.ru/maps/?text={encoded}"
    google = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    return yandex, google


def make_order_links(query):
    encoded = urllib.parse.quote(query)

    links = []

    links.append(("Яндекс Маркет", f"https://market.yandex.ru/search?text={encoded}"))
    links.append(("Ozon", f"https://www.ozon.ru/search/?text={encoded}"))
    links.append(("Wildberries", f"https://www.wildberries.ru/catalog/0/search.aspx?search={encoded}"))
    links.append(("Hoff", f"https://hoff.ru/search/?q={encoded}"))
    links.append(("Лемана ПРО", f"https://lemanapro.ru/search/?q={encoded}"))

    # Для еды и продуктов.
    links.append(("Яндекс Еда", "https://eda.yandex.ru/"))
    links.append(("Купер", "https://kuper.ru/"))
    links.append(("Самокат", "https://samokat.ru/"))

    return links


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
    """
    Пример:
    запомни телефон мамы +79991234567
    запомни номер папы 89991234567
    """

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
    """
    Пример:
    запомни телеграм жены 123456789
    запомни telegram мамы 987654321

    Важно: человек должен сам нажать Start у вашего Telegram-бота,
    иначе бот не сможет ему писать.
    """

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
    """
    Реальный звонок через навык невозможен.
    Поэтому отправляем ссылку tel: в Telegram.
    """

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
# Списки и память
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


# ---------------------------------------------------------
# Фоновый агент для Telegram
# ---------------------------------------------------------

async def background_agent_task(command, session_id, state_snapshot):
    """
    Долгие задачи выполняем в фоне:
    поиск, анализ, файл, отправка в Telegram.
    Для товаров теперь пытаемся выбрать самые дешёвые реальные варианты,
    а не просто отправлять ссылки на поиск.
    """

    try:
        state = copy.deepcopy(state_snapshot)
        query = extract_search_query(command)

        recipient_chat_id = get_telegram_recipient_chat_id(command)

        internet_context = ""
        links = []
        product_candidates = []

        # Карты
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

        # Если это покупка/товар/доставка — ищем именно товары с ценами
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

            # Если нашли товары с ценами — отправляем именно отсортированную подборку
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

            # Если цен не нашли — НЕ отправляем мусорные ссылки на поиск
            no_price_text = (
                f"Я поискала варианты по запросу:\n{query}\n\n"
                f"Но не смогла достоверно вытащить цены из найденных страниц. "
                f"Поэтому не буду выдавать обычные ссылки на поиск как будто это подборка.\n\n"
                f"Что можно сделать:\n"
                f"1. Уточнить запрос: бренд, модель, размер, цвет или бюджет.\n"
                f"2. Попросить: «найди на Авито в Краснодаре».\n"
                f"3. Попросить: «найди только новые товары» или «можно б/у».\n\n"
                f"Пример:\n"
                f"найди самые дешёвые серые шторы 200 на 250 в Краснодаре\n\n"
                f"Если хотите, я могу следующим запросом отправить просто ссылки на поиск, "
                f"но как подборку дешёвых товаров я их считать не буду."
            )

            state["last_result"] = no_price_text
            await send_telegram_message(no_price_text, chat_id=recipient_chat_id)
            save_state(session_id, state)
            return

        # Если не покупка, но интернет нужен — обычный поиск
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
            f"Подготовь результат для Telegram.\n\n"
            f"Правила:\n"
            f"1. Не выдавай обычные ссылки на поиск как готовую подборку.\n"
            f"2. Если это подбор товара и нет цен — честно скажи, что сравнить по цене не удалось.\n"
            f"3. Не выдумывай магазины, цены и ссылки.\n"
            f"4. Учитывай Краснодар и доставку в Краснодар.\n"
            f"5. Если это заказ еды или товара — не оформляй и не оплачивай заказ сам, только дай ссылки.\n"
            f"6. В конце дай короткий практический вывод.\n"
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

        # Для НЕтоварных задач ссылки можно добавлять.
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
# Главные endpoints
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

    # Короткое приветствие.
    if is_new or not command:
        save_state(session_id, state)
        return JSONResponse(make_response("Привет!"))

    # Выход.
    if text in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        save_state(session_id, state)
        return JSONResponse(make_response("Хорошо.", end_session=True))

    # Помощь.
    if text in ["помощь", "что ты умеешь", "команды"]:
        save_state(session_id, state)
        return JSONResponse(make_response(help_text()))

    # Если ранее был задан уточняющий вопрос — объединяем старую задачу и новый ответ.
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

    # Режимы.
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

        return JSONResponse(make_response(f"{title} включён."))

    # Сохранение Telegram-контакта.
    if text.startswith("запомни телеграм") or text.startswith("запомни telegram"):
        result = save_telegram_contact_from_command(command)

        if result:
            name, chat_id = result
            save_state(session_id, state)
            return JSONResponse(make_response(f"Запомнила Telegram для контакта: {name}."))

        save_state(session_id, state)
        return JSONResponse(make_response("Не смогла распознать Telegram chat id."))

    # Сохранение телефонного контакта.
    if text.startswith("запомни телефон") or text.startswith("запомни номер"):
        result = save_contact_from_command(command)

        if result:
            name, number = result
            save_state(session_id, state)
            return JSONResponse(make_response(f"Запомнила телефон: {name}."))

        save_state(session_id, state)
        return JSONResponse(make_response("Не смогла распознать номер."))

    # Запрос на звонок.
    if is_phone_call_request(command):
        answer = await handle_call_request(command)
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

    # Запомнить факт.
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

    # Автозапоминание имени.
    remembered_name = try_auto_remember_name(command, state)

    if remembered_name:
        save_state(session_id, state)
        return JSONResponse(make_response(f"Приятно познакомиться, {remembered_name}."))

    # Что помнишь.
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

    # Очистка памяти текущей сессии.
    if text in ["очисти память", "сотри память", "забудь все", "забудь всё"]:
        state["facts"] = []
        state["history"] = []
        state["last_result"] = ""
        state["last_links"] = []
        state["shopping_list"] = []
        state["pending_task"] = None

        save_state(session_id, state)
        return JSONResponse(make_response("Память очищена."))

    # Список покупок.
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

    # Отправка списка покупок в Telegram.
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

    # Скинуть последний результат в Telegram.
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

    # Уточняющие вопросы для покупок.
    # Важно: спрашиваем через колонку, а не пишем в Telegram.
    clarification = shopping_needs_clarification(command)

    if clarification:
        state["pending_task"] = {
            "type": "shopping_clarification",
            "original_command": command
        }

        save_state(session_id, state)
        return JSONResponse(make_response(clarification))

    # Если пользователь просит Telegram/файл/покупку/длинный поиск — запускаем задачу в фоне.
    # Так колонка не ждёт долго и не падает по таймауту.
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

    # Интернет-поиск без Telegram.
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

        if is_order_request(command):
            order_links = make_order_links(query)
            internet_context += (
                "Ссылки для самостоятельной проверки и оформления заказа:\n"
            )

            for title, url in order_links:
                internet_context += f"{title}: {url}\n"

            internet_context += (
                "\nВажно: не оформляй и не оплачивай заказ автоматически.\n\n"
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

    # Обычный вопрос к DeepSeek.
    try:
        answer, state = await ask_deepseek(command, state)
        state["last_result"] = answer
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

    except Exception as e:
        print(f"SERVER ERROR: {str(e)}", flush=True)
        save_state(session_id, state)
        return JSONResponse(make_response("Я не успела получить ответ. Попробуйте короче."))
