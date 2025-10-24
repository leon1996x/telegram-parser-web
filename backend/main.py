from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
import os
import shutil

app = FastAPI()
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

SESSIONS_DIR = "backend/sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

clients = {}  # Активные клиенты


@app.get("/", response_class=HTMLResponse)
async def index():
    return open("backend/templates/index.html", "r", encoding="utf-8").read()


@app.post("/login_file")
async def login_file(session_file: UploadFile):
    """Авторизация через .session файл"""
    path = os.path.join(SESSIONS_DIR, session_file.filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(session_file.file, f)

    # Имя сессии без расширения
    session_name = path.replace(".session", "")

    # Telethon всё равно требует непустые ID/HASH, но они могут быть любыми
    dummy_id = 12345
    dummy_hash = "0123456789abcdef0123456789abcdef"

    client = TelegramClient(session_name, dummy_id, dummy_hash)
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse("<h3>❌ Сессия недействительна или устарела</h3>")

    clients[session_file.filename] = client
    return HTMLResponse('<h3>✅ Успешный вход!</h3><br><a href="/chats">Показать чаты</a>')


@app.post("/login_manual")
async def login_manual(api_id: int = Form(...), api_hash: str = Form(...)):
    """Авторизация вручную (для случаев без .session)"""
    session_path = os.path.join(SESSIONS_DIR, "manual_login")
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse("<h3>❌ Авторизация требуется (код из Telegram)</h3>")

    clients["manual_login"] = client
    return HTMLResponse('<h3>✅ Вход выполнен вручную!</h3><br><a href="/chats">Показать чаты</a>')


@app.get("/chats", response_class=HTMLResponse)
async def get_chats():
    """Вывод чатов"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    dialogs = await client.get_dialogs(limit=100)

    html = "<h2>Список чатов:</h2><ul>"
    for d in dialogs:
        html += f"<li>{d.name or 'Без названия'} — <b>ID:</b> {d.id}</li>"
    html += "</ul>"

    return HTMLResponse(html)
