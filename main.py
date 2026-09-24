import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


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
Но помни: ответ будет озвучен голосом, поэтому не делай его слишком длинным.
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
    }
}


def get_state_from_request(data):
    """
    Берём состояние диалога из Яндекс.Диалогов.
    Это главное исправление.
    """
    state = data.get("state", {}).get("session", {})

    if not isinstance(state, dict):
        state = {}

    if "mode" not in state:
        state["mode"] = "обычный"

    if "facts" not in state:
        state["facts"] = []

    if "history" not in state:
        state["history"] = []

    return state


def make_response(text, session_id="", state=None, end_session=False):
    if state is None:
        state = {
            "mode": "обычный",
            "facts": [],
            "history": []
        }

    if not text:
        text = "Я не получила ответ."

    text = str(text).strip()

    # Слишком длинные ответы Алиса может плохо озвучивать.
    if len(text) > 950:
        text = text[:950] + "..."

    return {
        "version": "1.0",
        "session": {
            "session_id": session_id
        },
        "response": {
            "text": text,
            "tts": text,
            "end_session": end_session
        },

        # Вот это поле отвечает за память внутри текущего запуска навыка.
        "session_state": state
    }


def help_text():
    return """
Доступные команды:
режим обычный,
режим кратко,
режим учитель,
режим эксперт,
режим джарвис,
режим психолог,
режим критик,
режим переводчик,
режим ребёнок.

Также можно сказать:
запомни, что ...
что ты помнишь
очисти память
какой сейчас режим
"""


def modes_text():
    return """
Есть режимы:
обычный,
кратко,
учитель,
эксперт,
джарвис,
психолог,
критик,
переводчик,
ребёнок.
Например, скажите: режим джарвис.
"""


def normalize_mode_name(text):
    aliases = {
        "учителя": "учитель",
        "преподаватель": "учитель",
        "эксперта": "эксперт",
        "джервис": "джарвис",
        "джарвес": "джарвис",
        "jarvis": "джарвис",
        "психологический": "психолог",
        "психолога": "психолог",
        "критика": "критик",
        "перевода": "переводчик",
        "перевод": "переводчик",
        "детский": "ребёнок",
        "ребенок": "ребёнок",
        "ребёнка": "ребёнок",
        "ребенка": "ребёнок",
        "коротко": "кратко",
        "короткий": "кратко"
    }

    return aliases.get(text, text)


def build_messages(state, user_text):
    mode_name = state.get("mode", "обычный")
    mode = MODES.get(mode_name, MODES["обычный"])

    facts = state.get("facts", [])
    history = state.get("history", [])

    memory_text = ""

    if facts:
        memory_text += "\nВот что ты помнишь о пользователе:\n"
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

    # Добавляем последние реплики диалога.
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

    # Сохраняем историю в session_state.
    history = state.get("history", [])

    history.append({
        "role": "user",
        "content": user_text
    })

    history.append({
        "role": "assistant",
        "content": answer
    })

    # Важно: не делаем историю бесконечной, иначе ответы начнут тормозить.
    state["history"] = history[-12:]

    print(f"HISTORY LENGTH AFTER: {len(state.get('history', []))}", flush=True)

    return answer, state


@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "DeepSeek Alice webhook is running",
        "memory": "session_state enabled",
        "features": [
            "modes",
            "session memory",
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
    lower = command.lower()

    state = get_state_from_request(data)

    print("YANDEX REQUEST RECEIVED", flush=True)
    print(f"SESSION ID: {session_id}", flush=True)
    print(f"IS NEW SESSION: {is_new}", flush=True)
    print(f"COMMAND: {command}", flush=True)
    print(f"STATE AT START: {state}", flush=True)

    # Новый запуск навыка
    if is_new or not command:
        mode_name = state.get("mode", "обычный")
        greeting = (
            f"Голосовой интеллект активирован. "
            f"Сейчас включён режим: {mode_name}. "
            f"Скажите помощь, чтобы узнать команды."
        )
        return JSONResponse(make_response(greeting, session_id, state))

    # Выход
    if lower in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        return JSONResponse(
            make_response("Хорошо, завершаю разговор.", session_id, state, end_session=True)
        )

    # Помощь
    if lower in ["помощь", "что ты умеешь", "команды", "список команд"]:
        return JSONResponse(make_response(help_text(), session_id, state))

    # Список режимов
    if lower in ["режимы", "какие есть режимы", "список режимов"]:
        return JSONResponse(make_response(modes_text(), session_id, state))

    # Текущий режим
    if lower in ["какой режим", "какой сейчас режим", "текущий режим"]:
        mode_name = state.get("mode", "обычный")
        title = MODES.get(mode_name, MODES["обычный"])["title"]
        return JSONResponse(
            make_response(f"Сейчас включён: {title}.", session_id, state)
        )

    # Переключение режима
    if lower.startswith("режим "):
        requested_mode = lower.replace("режим ", "").strip()
        requested_mode = normalize_mode_name(requested_mode)

        if requested_mode in MODES:
            state["mode"] = requested_mode
            title = MODES[requested_mode]["title"]

            if requested_mode == "джарвис":
                answer = "Режим Джарвис активирован. Слушаю вас."
            else:
                answer = f"{title} включён."

            return JSONResponse(make_response(answer, session_id, state))

        return JSONResponse(
            make_response(
                "Такого режима нет. Скажите: режимы, чтобы услышать список.",
                session_id,
                state
            )
        )

    # Запомнить факт
    if lower.startswith("запомни"):
        fact = command

        replacements = [
            "запомни, что",
            "Запомни, что",
            "запомни что",
            "Запомни что",
            "запомни",
            "Запомни"
        ]

        for r in replacements:
            fact = fact.replace(r, "", 1)

        fact = fact.strip(" .,!?")

        if not fact:
            return JSONResponse(
                make_response("Что именно запомнить?", session_id, state)
            )

        facts = state.get("facts", [])
        facts.append(fact)
        state["facts"] = facts[-20:]

        return JSONResponse(
            make_response(f"Запомнила: {fact}.", session_id, state)
        )

    # Что помнишь?
    if lower in [
        "что ты помнишь",
        "что ты обо мне помнишь",
        "что ты запомнила",
        "что ты знаешь обо мне"
    ]:
        facts = state.get("facts", [])

        if not facts:
            return JSONResponse(
                make_response("Пока я ничего не запомнила.", session_id, state)
            )

        text = "Я помню вот что: " + "; ".join(facts[-10:])
        return JSONResponse(make_response(text, session_id, state))

    # Очистка памяти
    if lower in [
        "очисти память",
        "сотри память",
        "забудь всё",
        "забудь все",
        "очистить память"
    ]:
        state["facts"] = []
        state["history"] = []
        return JSONResponse(
            make_response("Память очищена.", session_id, state)
        )

    # Проверка истории
    if lower in [
        "проверка памяти",
        "проверь память",
        "сколько сообщений в памяти"
    ]:
        history_count = len(state.get("history", []))
        facts_count = len(state.get("facts", []))
        mode_name = state.get("mode", "обычный")

        text = (
            f"Память работает. "
            f"В истории сейчас сообщений: {history_count}. "
            f"Запомненных фактов: {facts_count}. "
            f"Текущий режим: {mode_name}."
        )

        return JSONResponse(make_response(text, session_id, state))

    if not DEEPSEEK_API_KEY:
        return JSONResponse(
            make_response("Ошибка настройки. Не найден ключ DeepSeek.", session_id, state)
        )

    # Обычный вопрос отправляем в DeepSeek
    try:
        answer, state = await ask_deepseek(command, state)
        return JSONResponse(make_response(answer, session_id, state))
    except Exception as e:
        print(f"SERVER ERROR: {str(e)}", flush=True)
        return JSONResponse(
            make_response(
                "Я не успела получить ответ. Попробуйте задать вопрос короче.",
                session_id,
                state
            )
        )
