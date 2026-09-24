import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """
Ты голосовой помощник внутри навыка Алисы.
Отвечай по-русски, понятно и не слишком длинно.
Не говори, что ты ChatGPT.
Если пользователь просит подробности, объясняй простыми словами.
"""


def make_response(text, session_id="", end_session=False):
    if not text:
        text = "Я не получила ответ."

    text = text.strip()

    if len(text) > 900:
        text = text[:900] + "..."

    return {
        "version": "1.0",
        "response": {
            "text": text,
            "tts": text,
            "end_session": end_session
        },
        "session": {
            "session_id": session_id
        }
    }


@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "DeepSeek Alice webhook is running"
    }


@app.post("/")
async def alice_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(make_response("Ошибка чтения запроса."))

    session = data.get("session", {})
    session_id = session.get("session_id", "")

    request_data = data.get("request", {})
    command = request_data.get("command", "").strip()

    is_new = session.get("new", False)

    if is_new or not command:
        return JSONResponse(
            make_response(
                "Привет! Я умный собеседник. Задайте мне вопрос.",
                session_id
            )
        )

    lower = command.lower()

    if lower in ["хватит", "стоп", "выход", "закончить", "завершить"]:
        return JSONResponse(
            make_response(
                "Хорошо, завершаю разговор.",
                session_id,
                end_session=True
            )
        )

    if not DEEPSEEK_API_KEY:
        return JSONResponse(
            make_response(
                "Ошибка настройки. Не найден ключ DeepSeek.",
                session_id
            )
        )

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT
                        },
                        {
                            "role": "user",
                            "content": command
                        }
                    ],
                    "temperature": 0.7,
                    "max_tokens": 400
                }
            )

        if response.status_code != 200:
            return JSONResponse(
                make_response(
                    "DeepSeek сейчас не ответил. Попробуйте ещё раз.",
                    session_id
                )
            )

        result = response.json()
        answer = result["choices"][0]["message"]["content"]

        return JSONResponse(
            make_response(answer, session_id)
        )

    except Exception:
        return JSONResponse(
            make_response(
                "Я не успела получить ответ. Попробуйте задать вопрос короче.",
                session_id
            )
        )
