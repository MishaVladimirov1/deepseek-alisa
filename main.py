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


# Память текущих сессий навыка
SERVER_SESSIONS = {}

# Простая общая память.
# Внимание: на бесплатном Render она может сброситься после перезапуска сервера.
GLOBAL_PROFILE = {
    "facts": [],
    "contacts": {}
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
        "max_tokens": 200,
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
        "max_tokens": 700,
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
        "max_tokens": 650,
        "temperature": 0.6
    },

    "покупки": {
        "title": "Режим покупок",
        "prompt": """
Ты помощник по покупкам.
Помогай искать товары, сравнивать варианты, проверять риски, составлять список.
Не оформляй и не оплачивай заказы самостоятельно.
Всегда предлагай пользователю самому подтвердить покупку.
""",
        "max_tokens": 650,
        "temperature": 0.5
    },

    "дом": {
        "title": "Режим дом",
        "prompt": """
Ты домашний помощник.
Помогай с рецептами, бытовыми задачами, ремонтом, покупками, списками дел и инструкциями.
Отвечай просто и практично.
""",
        "max_tokens": 600,
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
        "max_tokens": 600,
        "temperature": 0.6
    }
}


# -----------------------------
# Базовые функции
# -----------------------------

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

    # Для колонки лучше коротко
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


# -----------------------------
# Режимы
# -----------------------------

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


# -----------------------------
# Telegram
# -----------------------------

async def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM NOT CONFIGURED", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    if len(text) > 3900:
        text = text[:3900] + "\n\n...текст был сокращён."

    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": False
            }
        )

    print(f"TELEGRAM MESSAGE STATUS: {response.status_code}", flush=True)
    return response.status_code == 200


async def send_telegram_file(filename, content):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM NOT CONFIGURED", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"

    if not filename:
        filename = "document.txt"

    if not content:
        content = "Пустой документ."

    file_bytes = io.BytesIO(content.encode("utf-8"))
    file_bytes.name = filename

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": filename
            },
            files={
                "document": (filename, file_bytes, "text/plain")
            }
        )

    print(f"TELEGRAM FILE STATUS: {response.status_code}", flush=True)
    return response.status_code == 200


# -----------------------------
# Интернет-поиск
# -----------------------------

async def web_search(query, max_results=5):
    if not TAVILY_API_KEY:
        return {
            "ok": False,
            "error": "TAVILY_API_KEY не настроен.",
            "results": []
        }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "max_results": max_results
                }
            )

        print(f"TAVILY STATUS: {response.status_code}", flush=True)

        if response.status_code != 200:
            return {
                "ok": False,
                "error": f"Ошибка поиска: {response.status_code}",
                "results": []
            }

        data = response.json()
        results = data.get("results", [])

        clean_results = []

        for item in results:
            clean_results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", "")
            })

        return {
            "ok": True,
            "error": "",
            "results": clean_results
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
            text += f"{content[:500]}\n"
        if url:
            text += f"Ссылка: {url}\n"
        text += "\n"

    return text.strip()


# -----------------------------
# DeepSeek
# -----------------------------

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
        memory_text += "\nДанные из интернета:\n"
        memory_text += internet_context[:6000]
        memory_text += "\n"

    system_prompt = mode["prompt"] + """

У тебя есть внешние инструменты, которые выполняет сервер:
- интернет-поиск;
- отправка сообщений в Telegram;
- отправка файлов;
- создание ссылок на карты;
- подготовка ссылок для заказа товаров и еды.

Важные правила:
1. Не выдумывай ссылки. Используй только ссылки из поиска или явно созданные ссылки на карты.
2. Если пользователь просит отправить что-то на телефон, результат должен быть пригоден для Telegram.
3. Не оформляй и не оплачивай заказы самостоятельно.
4. Для покупок, еды и доставки готовь варианты и ссылки, но финальное подтверждение делает пользователь.
5. Если данных мало, честно скажи об этом.
6. Для голосового ответа отвечай коротко, а подробности лучше отправлять в Telegram.
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
    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    messages = build_messages(state, user_text, internet_context)

    max_tokens = mode["max_tokens"]
    if long_answer:
        max_tokens = 1200

    print(f"DEEPSEEK REQUEST: {user_text}", flush=True)
    print(f"MODE: {mode_name}", flush=True)

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
        print(f"DEEPSEEK ERROR: {response.text}", flush=True)
        return "DeepSeek сейчас не ответил. Попробуйте позже.", state

    data = response.json()
    answer = data["choices"][0]["message"]["content"].strip()

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


# -----------------------------
# Определение намерений
# -----------------------------

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
        "ссылки мне"
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
        "стоит",
        "где купить",
        "купить",
        "товар",
        "отзывы",
        "адрес",
        "рядом",
        "ближайший",
        "ближайшая",
        "маршрут",
        "карта",
        "доставка",
        "заказать",
        "закажи",
        "еда",
        "ресторан",
        "кафе",
        "аптека",
        "магазин",
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
        "еда",
        "пицца",
        "суши",
        "роллы",
        "бургер",
        "продукты"
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


def extract_search_query(command):
    text = command.strip()

    remove_phrases = [
        "найди в интернете",
        "поищи в интернете",
        "посмотри в интернете",
        "найди",
        "поищи",
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

    return text


# -----------------------------
# Карты, заказы, телефон
# -----------------------------

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

    # Для еды удобнее давать ссылки-поиски
    links.append(("Яндекс Еда", f"https://eda.yandex.ru/"))
    links.append(("Купер", f"https://kuper.ru/"))
    links.append(("Самокат", f"https://samokat.ru/"))

    return links


def extract_phone_number(text):
    # Ищем номер телефона в тексте
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
    cleaned = cleaned.replace(normalize_text(number), "")
    cleaned = re.sub(r"\d+", "", cleaned)
    name = cleaned.strip()

    if not name:
        name = "контакт"

    GLOBAL_PROFILE["contacts"][name] = number

    return name, number


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


# -----------------------------
# Списки и память
# -----------------------------

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


# -----------------------------
# Фоновый агент для Telegram
# -----------------------------

async def background_agent_task(command, session_id, state_snapshot):
    """
    Долгие задачи выполняем в фоне:
    поиск, анализ, файл, отправка в Telegram.
    Колонка быстро отвечает: "Сделаю и отправлю".
    """

    try:
        state = copy.deepcopy(state_snapshot)
        query = extract_search_query(command)

        internet_context = ""
        links = []

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

        # Заказы
        if is_order_request(command):
            order_links = make_order_links(query)

            order_text = (
                f"🛒 Подготовка заказа по запросу:\n\n"
                f"{query}\n\n"
                f"Я не оформляю и не оплачиваю заказы автоматически. "
                f"Ниже ссылки, где можно вручную проверить и оформить заказ:\n\n"
            )

            for title, url in order_links:
                order_text += f"- {title}: {url}\n"
                links.append(url)

            internet_context += order_text + "\n\n"

        # Обычный интернет-поиск
        if needs_web(command):
            search = await web_search(query, max_results=5)

            if search["ok"]:
                results_text = format_search_results(search["results"])
                internet_context += "\nРезультаты интернет-поиска:\n" + results_text

                for item in search["results"]:
                    if item.get("url"):
                        links.append(item["url"])
            else:
                internet_context += f"\nИнтернет-поиск не сработал: {search['error']}"

        prompt = (
            f"Задача пользователя: {command}\n\n"
            f"Подготовь полезный результат для отправки в Telegram. "
            f"Если это товар, еда или заказ — дай варианты и ссылки, но напомни, что финальное оформление делает пользователь. "
            f"Если это рецепт — дай список продуктов и шаги. "
            f"Если это таблица — сделай аккуратную текстовую таблицу. "
            f"Если это документ — оформи как готовый текст документа."
        )

        answer, state = await ask_deepseek(
            prompt,
            state,
            internet_context=internet_context,
            long_answer=True
        )

        if links:
            state["last_links"] = links[-20:]

        state["last_result"] = answer

        # Отправляем файл, если пользователь просил файл/документ/таблицу
        if wants_file(command):
            filename = "assistant_result.txt"

            if "таблиц" in normalize_text(command):
                filename = "table.txt"
            elif "рецепт" in normalize_text(command):
                filename = "recipe.txt"
            elif "документ" in normalize_text(command):
                filename = "document.txt"

            await send_telegram_file(filename, answer)
            await send_telegram_message("Готово. Файл отправлен выше.")
        else:
            await send_telegram_message(answer)

        save_state(session_id, state)

    except Exception as e:
        print(f"BACKGROUND TASK ERROR: {str(e)}", flush=True)
        await send_telegram_message(
            f"Произошла ошибка при выполнении задачи:\n{str(e)}"
        )


# -----------------------------
# Главные endpoints
# -----------------------------

@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "DeepSeek Alice agent is running",
        "greeting": "Привет!",
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "internet_configured": bool(TAVILY_API_KEY),
        "memory": "server session memory",
        "active_sessions": len(SERVER_SESSIONS),
        "features": [
            "DeepSeek",
            "internet search",
            "Telegram messages",
            "Telegram files",
            "maps links",
            "safe order preparation",
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

    # Короткое приветствие
    if is_new or not command:
        save_state(session_id, state)
        return JSONResponse(make_response("Привет!"))

    # Выход
    if text in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        save_state(session_id, state)
        return JSONResponse(make_response("Хорошо.", end_session=True))

    # Помощь
    if text in ["помощь", "что ты умеешь", "команды"]:
        save_state(session_id, state)
        return JSONResponse(make_response(help_text()))

    # Режимы
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

    # Сохранение контакта
    if text.startswith("запомни телефон") or text.startswith("запомни номер"):
        result = save_contact_from_command(command)

        if result:
            name, number = result
            save_state(session_id, state)
            return JSONResponse(make_response(f"Запомнила телефон: {name}."))

        save_state(session_id, state)
        return JSONResponse(make_response("Не смогла распознать номер."))

    # Запрос на звонок
    if is_phone_call_request(command):
        answer = await handle_call_request(command)
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

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

    # Автозапоминание имени
    remembered_name = try_auto_remember_name(command, state)

    if remembered_name:
        save_state(session_id, state)
        return JSONResponse(make_response(f"Приятно познакомиться, {remembered_name}."))

    # Что помнишь
    if text in [
        "что ты помнишь",
        "что ты обо мне помнишь",
        "что ты знаешь обо мне"
    ]:
        all_facts = GLOBAL_PROFILE.get("facts", []) + state.get("facts", [])
        contacts = GLOBAL_PROFILE.get("contacts", {})

        if not all_facts and not contacts:
            save_state(session_id, state)
            return JSONResponse(make_response("Пока я ничего не запомнила."))

        result = ""

        if all_facts:
            result += "Я помню: " + "; ".join(all_facts[-10:]) + ". "

        if contacts:
            result += "Есть контакты: " + ", ".join(contacts.keys()) + "."

        save_state(session_id, state)
        return JSONResponse(make_response(result))

    # Очистка памяти
    if text in ["очисти память", "сотри память", "забудь все", "забудь всё"]:
        state["facts"] = []
        state["history"] = []
        state["last_result"] = ""
        state["last_links"] = []
        state["shopping_list"] = []

        save_state(session_id, state)
        return JSONResponse(make_response("Память очищена."))

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

    # Скинуть последний результат в Telegram
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

        ok = await send_telegram_message(last)

        save_state(session_id, state)

        if ok:
            return JSONResponse(make_response("Отправила в Телеграм."))
        else:
            return JSONResponse(make_response("Телеграм не настроен или не ответил."))

    # Если пользователь просит Telegram/файл/длинный поиск — запускаем задачу в фоне
    if wants_telegram(command) or wants_file(command):
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
        else:
            return JSONResponse(make_response("Сделаю и отправлю в Телеграм."))

    # Интернет-поиск без Telegram
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

        if not DEEPSEEK_API_KEY:
            save_state(session_id, state)
            return JSONResponse(make_response("DeepSeek API не настроен."))

        answer, state = await ask_deepseek(
            command,
            state,
            internet_context=internet_context,
            long_answer=False
        )

        state["last_result"] = answer
        save_state(session_id, state)

        return JSONResponse(make_response(answer))

    # Обычный вопрос к DeepSeek
    if not DEEPSEEK_API_KEY:
        save_state(session_id, state)
        return JSONResponse(make_response("DeepSeek API не настроен."))

    try:
        answer, state = await ask_deepseek(command, state)
        state["last_result"] = answer
        save_state(session_id, state)
        return JSONResponse(make_response(answer))

    except Exception as e:
        print(f"SERVER ERROR: {str(e)}", flush=True)
        save_state(session_id, state)
        return JSONResponse(make_response("Я не успела получить ответ. Попробуйте короче."))
