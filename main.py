import os
import time
import re
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# Память на сервере по session_id Яндекса
SERVER_SESSIONS = {}


MODES = {
    "обычный": {
        "title": "Обычный режим",
        "prompt": """
Ты голосовой помощник внутри навыка Алисы.
Отвечай по-русски, понятно, дружелюбно и не слишком длинно.
Не говори, что ты ChatGPT.
Если вопрос сложный, объясняй простыми словами.
""",
        "max_tokens": 450,
        "temperature": 0.7
    },

    "кратко": {
        "title": "Краткий режим",
        "prompt": """
Ты голосовой помощник внутри навыка Алисы.
Отвечай максимально коротко: 1-3 предложения.
Без воды. Только суть.
""",
        "max_tokens": 180,
        "temperature": 0.5
    },

    "учитель": {
        "title": "Режим учителя",
        "prompt": """
Ты добрый и терпеливый учитель.
Объясняй простыми словами, с примерами.
Если тема сложная, разбивай объяснение на маленькие шаги.
Отвечай по-русски.
""",
        "max_tokens": 550,
        "temperature": 0.6
    },

    "эксперт": {
        "title": "Экспертный режим",
        "prompt": """
Ты сильный эксперт и аналитик.
Отвечай точно, структурированно и глубоко.
Давай практические выводы.
Если есть спорные моменты, отмечай их.
Помни: ответ будет озвучен голосом, поэтому не делай его слишком длинным.
""",
        "max_tokens": 650,
        "temperature": 0.5
    },

    "джарвис": {
        "title": "Режим Джарвис",
        "prompt": """
Ты персональный голосовой ассистент в стиле умного дворецкого и аналитика.
Обращайся уважительно, уверенно и спокойно.
Отвечай по-русски.
Стиль: чётко, немного кинематографично, но без перебора.
Не называй себя ChatGPT.
Если пользователь что-то уже говорил ранее, учитывай это.
""",
        "max_tokens": 500,
        "temperature": 0.7
    },

    "психолог": {
        "title": "Режим психолога",
        "prompt": """
Ты спокойный поддерживающий собеседник.
Помогай человеку разобраться в мыслях и эмоциях.
Не ставь медицинские диагнозы.
Не заменяй врача или психотерапевта.
Отвечай мягко, с уважением и по-русски.
""",
        "max_tokens": 500,
        "temperature": 0.7
    },

    "критик": {
        "title": "Режим критика",
        "prompt": """
Ты строгий, но полезный критик.
Твоя задача — искать слабые места в идеях пользователя.
Говори прямо, но не грубо.
После критики предлагай, как улучшить идею.
Отвечай по-русски.
""",
        "max_tokens": 550,
        "temperature": 0.6
    },

    "переводчик": {
        "title": "Режим переводчика",
        "prompt": """
Ты переводчик и преподаватель языков.
Если пользователь просит перевести — переводи.
Если есть ошибки — мягко исправляй.
Если нужно, объясняй значение фраз.
Отвечай по-русски, если пользователь не попросил другой язык.
""",
        "max_tokens": 450,
        "temperature": 0.4
    },

    "ребёнок": {
        "title": "Режим объяснения для ребёнка",
        "prompt": """
Объясняй всё очень простыми словами, как ребёнку 7 лет.
Используй понятные примеры из жизни.
Не используй сложные термины без объяснения.
Отвечай по-русски.
""",
        "max_tokens": 450,
        "temperature": 0.6
    },

    "совет": {
        "title": "Режим совет директоров",
        "prompt": """
Ты моделируешь совет директоров из пяти экспертов:
1. стратег,
2. финансист,
3. маркетолог,
4. технический эксперт,
5. критик рисков.

На любой вопрос отвечай так:
Стратег: ...
Финансист: ...
Маркетолог: ...
Технический эксперт: ...
Критик рисков: ...
Итог: ...

Отвечай по-русски и достаточно кратко, потому что ответ будет озвучен голосом.
""",
        "max_tokens": 700,
        "temperature": 0.7
    }
}


def create_empty_state():
    return {
        "mode": "обычный",
        "facts": [],
        "history": [],
        "created_at": time.time(),
        "last_seen": time.time()
    }


def cleanup_old_sessions():
    now = time.time()
    max_age_seconds = 6 * 60 * 60

    old_keys = []

    for session_id, state in SERVER_SESSIONS.items():
        last_seen = state.get("last_seen", 0)
        if now - last_seen > max_age_seconds:
            old_keys.append(session_id)

    for key in old_keys:
        del SERVER_SESSIONS[key]


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
        text = "Я не получила ответ."

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


def help_text():
    return """
Команды:
режим обычный,
режим кратко,
режим учитель,
режим эксперт,
режим джарвис,
режим психолог,
режим критик,
режим переводчик,
режим ребёнок,
режим совет.

Можно сказать:
запомни, что ...
что ты помнишь
очисти память
какой сейчас режим
проверка памяти
"""


def modes_text():
    return """
Доступные режимы:
обычный,
кратко,
учитель,
эксперт,
джарвис,
психолог,
критик,
переводчик,
ребёнок,
совет.
Например: режим джарвис.
"""


def normalize_text(text):
    text = text.lower().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_mode_name(text):
    text = normalize_text(text)

    aliases = {
        "обычный": "обычный",
        "стандартный": "обычный",

        "кратко": "кратко",
        "коротко": "кратко",
        "короткий": "кратко",

        "учитель": "учитель",
        "учителя": "учитель",
        "преподаватель": "учитель",

        "эксперт": "эксперт",
        "эксперта": "эксперт",

        "джарвис": "джарвис",
        "джервис": "джарвис",
        "джарвес": "джарвис",
        "jarvis": "джарвис",

        "психолог": "психолог",
        "психолога": "психолог",
        "психологический": "психолог",

        "критик": "критик",
        "критика": "критик",

        "переводчик": "переводчик",
        "перевод": "переводчик",
        "перевода": "переводчик",

        "ребенок": "ребёнок",
        "ребенка": "ребёнок",
        "детский": "ребёнок",

        "совет": "совет",
        "совет директоров": "совет",
        "директоров": "совет"
    }

    return aliases.get(text, text)


def detect_mode_command(command):
    """
    Возвращает название режима, если пользователь просит включить режим.
    Например:
    - режим джарвис
    - включи режим джарвис
    - переключись на режим критик
    - джарвис
    """

    text = normalize_text(command)

    # Если пользователь просто сказал название режима
    direct_mode = normalize_mode_name(text)
    if direct_mode in MODES:
        return direct_mode

    phrases_to_remove = [
        "включи режим",
        "включить режим",
        "переключи режим на",
        "переключись на режим",
        "переключись в режим",
        "поставь режим",
        "сделай режим",
        "активируй режим",
        "запусти режим",
        "режим"
    ]

    for phrase in phrases_to_remove:
        if text.startswith(phrase):
            mode_part = text.replace(phrase, "", 1).strip()
            mode_name = normalize_mode_name(mode_part)
            if mode_name in MODES:
                return mode_name

    return None


def extract_fact(command):
    fact = command.strip()

    lower_fact = normalize_text(fact)

    starts = [
        "запомни что",
        "запомни"
    ]

    for start in starts:
        if lower_fact.startswith(start):
            # Удаляем по количеству символов примерно из оригинальной строки проще:
            words = fact.split()
            if words and words[0].lower().replace("ё", "е") == "запомни":
                fact = " ".join(words[1:])
            break

    fact = fact.strip(" .,!?:;")

    if fact.lower().startswith("что "):
        fact = fact[4:].strip(" .,!?:;")

    return fact


def try_auto_remember_name(command, state):
    """
    Автоматически запоминает имя, если пользователь сказал:
    - меня зовут Миша
    - моё имя Миша
    """

    text = command.strip()
    low = normalize_text(text)

    name = None

    if low.startswith("меня зовут "):
        name = text.split(" ", 2)[-1].strip(" .,!?:;")

    elif low.startswith("мое имя "):
        name = text.split(" ", 2)[-1].strip(" .,!?:;")

    elif low.startswith("моё имя "):
        name = text.split(" ", 2)[-1].strip(" .,!?:;")

    if name:
        fact = f"пользователя зовут {name}"

        facts = state.get("facts", [])

        # Не плодим одинаковые факты про имя
        facts = [f for f in facts if "пользователя зовут" not in f.lower()]
        facts.append(fact)

        state["facts"] = facts[-20:]

        return name

    return None


def build_messages(state, user_text):
    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    facts = state.get("facts", [])
    history = state.get("history", [])

    memory_text = ""

    if facts:
        memory_text += "\nВот что ты точно знаешь о пользователе:\n"
        for fact in facts[-10:]:
            memory_text += f"- {fact}\n"

    if history:
        memory_text += "\nУчитывай историю текущего разговора.\n"

    system_prompt = mode["prompt"] + "\n" + memory_text

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


async def ask_deepseek(user_text, state):
    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    messages = build_messages(state, user_text)

    print(f"DEEPSEEK REQUEST: {user_text}", flush=True)
    print(f"CURRENT MODE: {mode_name}", flush=True)
    print(f"FACTS BEFORE: {state.get('facts', [])}", flush=True)
    print(f"HISTORY LENGTH BEFORE: {len(state.get('history', []))}", flush=True)

    async with httpx.AsyncClient(timeout=4.2) as client:
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
                "max_tokens": mode["max_tokens"]
            }
        )

    print(f"DEEPSEEK RESPONSE STATUS: {response.status_code}", flush=True)

    if response.status_code != 200:
        print(f"DEEPSEEK ERROR BODY: {response.text}", flush=True)
        return "DeepSeek сейчас не ответил. Попробуйте ещё раз чуть позже.", state

    result = response.json()
    answer = result["choices"][0]["message"]["content"].strip()

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

    print(f"HISTORY LENGTH AFTER: {len(state.get('history', []))}", flush=True)

    return answer, state


@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "DeepSeek Alice webhook is running",
        "memory": "server session_id memory enabled",
        "greeting": "Привет!",
        "active_sessions": len(SERVER_SESSIONS),
        "features": [
            "modes",
            "server memory",
            "deepseek",
            "voice assistant"
        ]
    }


@app.post("/")
async def alice_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(make_response("Ошибка чтения запроса."))

    session = data.get("session", {})
    session_id = session.get("session_id", "")
    is_new = session.get("new", False)

    request_data = data.get("request", {})
    command = request_data.get("command", "").strip()
    lower = command.lower().strip()

    state = get_state(session_id, is_new=is_new)

    print("YANDEX REQUEST RECEIVED", flush=True)
    print(f"SESSION ID: {session_id}", flush=True)
    print(f"IS NEW SESSION: {is_new}", flush=True)
    print(f"COMMAND: {command}", flush=True)
    print(f"SERVER STATE AT START: {state}", flush=True)
    print(f"ACTIVE SERVER SESSIONS: {len(SERVER_SESSIONS)}", flush=True)

    # Новый запуск навыка — короткое приветствие
    if is_new or not command:
        save_state(session_id, state)
        return JSONResponse(make_response("Привет!"))

    # Выход
    if normalize_text(command) in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        save_state(session_id, state)
        return JSONResponse(
            make_response("Хорошо, завершаю разговор.", end_session=True)
        )

    # Помощь
    if normalize_text(command) in ["помощь", "что ты умеешь", "команды", "список команд"]:
        save_state(session_id, state)
        return JSONResponse(make_response(help_text()))

    # Список режимов
    if normalize_text(command) in ["режимы", "какие есть режимы", "список режимов"]:
        save_state(session_id, state)
        return JSONResponse(make_response(modes_text()))

    # Текущий режим
    if normalize_text(command) in ["какой режим", "какой сейчас режим", "текущий режим"]:
        mode_name = state.get("mode", "обычный")
        title = MODES.get(mode_name, MODES["обычный"])["title"]

        save_state(session_id, state)
        return JSONResponse(
            make_response(f"Сейчас включён: {title}.")
        )

    # Переключение режима
    detected_mode = detect_mode_command(command)

    if detected_mode:
        state["mode"] = detected_mode
        title = MODES[detected_mode]["title"]

        save_state(session_id, state)

        print(f"MODE SAVED: {state['mode']}", flush=True)

        if detected_mode == "джарвис":
            return JSONResponse(make_response("Режим Джарвис активирован."))
        elif detected_mode == "совет":
            return JSONResponse(make_response("Совет директоров собран."))
        else:
            return JSONResponse(make_response(f"{title} включён."))

    # Запомнить факт
    if normalize_text(command).startswith("запомни"):
        fact = extract_fact(command)

        if not fact:
            save_state(session_id, state)
            return JSONResponse(make_response("Что именно запомнить?"))

        facts = state.get("facts", [])
        facts.append(fact)
        state["facts"] = facts[-20:]

        save_state(session_id, state)

        print(f"FACT SAVED: {fact}", flush=True)
        print(f"ALL FACTS NOW: {state.get('facts', [])}", flush=True)

        return JSONResponse(
            make_response(f"Запомнила: {fact}.")
        )

    # Автоматически запоминаем имя
    remembered_name = try_auto_remember_name(command, state)

    if remembered_name:
        save_state(session_id, state)
        return JSONResponse(
            make_response(f"Приятно познакомиться, {remembered_name}.")
        )

    # Что помнишь?
    if normalize_text(command) in [
        "что ты помнишь",
        "что ты обо мне помнишь",
        "что ты запомнила",
        "что ты знаешь обо мне"
    ]:
        facts = state.get("facts", [])

        if not facts:
            save_state(session_id, state)
            return JSONResponse(make_response("Пока я ничего не запомнила."))

        text = "Я помню вот что: " + "; ".join(facts[-10:])

        save_state(session_id, state)
        return JSONResponse(make_response(text))

    # Очистка памяти
    if normalize_text(command) in [
        "очисти память",
        "сотри память",
        "забудь все",
        "очистить память"
    ]:
        state["facts"] = []
        state["history"] = []

        save_state(session_id, state)
        return JSONResponse(make_response("Память очищена."))

    # Проверка памяти
    if normalize_text(command) in [
        "проверка памяти",
        "проверь память",
        "сколько сообщений в памяти"
    ]:
        history_count = len(state.get("history", []))
        facts_count = len(state.get("facts", []))
        mode_name = state.get("mode", "обычный")

        text = (
            f"Память работает. "
            f"В истории сообщений: {history_count}. "
            f"Фактов: {facts_count}. "
            f"Режим: {mode_name}."
        )

        save_state(session_id, state)
        return JSONResponse(make_response(text))

    if not DEEPSEEK_API_KEY:
        save_state(session_id, state)
        return JSONResponse(
            make_response("Ошибка настройки. Не найден ключ DeepSeek.")
        )

    # Обычный вопрос отправляем в DeepSeek
    try:
        answer, state = await ask_deepseek(command, state)
        save_state(session_id, state)
        return JSONResponse(make_response(answer))
    except Exception as e:
        print(f"SERVER ERROR: {str(e)}", flush=True)
        save_state(session_id, state)
        return JSONResponse(
            make_response("Я не успела получить ответ. Попробуйте задать вопрос короче.")
        )
