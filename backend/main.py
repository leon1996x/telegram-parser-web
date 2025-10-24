from fastapi import FastAPI, Form, UploadFile, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
import os
import shutil

app = FastAPI()
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

SESSIONS_DIR = "backend/sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Храним активных клиентов
clients = {}

@app.get("/", response_class=HTMLResponse)
async def index():
    return open("backend/templates/index.html", "r", encoding="utf-8").read()


@app.post("/login_file")
async def login_file(session_file: UploadFile):
    path = os.path.join(SESSIONS_DIR, session_file.filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(session_file.file, f)

    # Загружаем сессию
    client = TelegramClient(path.replace(".session", ""), 0, "")
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse("<h3>Сессия недействительна</h3>")

    clients[session_file.filename] = client
    return HTMLResponse('<a href="/chats">Показать чаты</a>')


@app.post("/login_manual")
async def login_manual(session_name: str = Form(...), api_id: int = Form(...), api_hash: str = Form(...)):
    session_path = os.path.join(SESSIONS_DIR, session_name)
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(input("Введите номер телефона: "))
        return HTMLResponse("<h3>Нужно ввести код авторизации (пока вручную)</h3>")

    clients[session_name] = client
    return HTMLResponse('<a href="/chats">Показать чаты</a>')


@app.get("/chats", response_class=HTMLResponse)
async def get_chats():
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    # Берем первый активный клиент
    client = list(clients.values())[0]
    dialogs = await client.get_dialogs(limit=100)

    html = "<h2>Список чатов:</h2><ul>"
    for d in dialogs:
        html += f"<li>{d.name} — ID: {d.id}</li>"
    html += "</ul>"

    return HTMLResponse(html)
