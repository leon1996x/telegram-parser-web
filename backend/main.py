from fastapi import FastAPI, Form, UploadFile, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
import os
import shutil

app = FastAPI()
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

SESSIONS_DIR = "backend/sessions"
AVATAR_DIR = "backend/static/avatars"
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)

clients = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("backend/templates/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/login_file")
async def login_file(session_file: UploadFile):
    """Авторизация через .session файл"""
    path = os.path.join(SESSIONS_DIR, session_file.filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(session_file.file, f)

    session_name = path.replace(".session", "")
    dummy_id = 12345
    dummy_hash = "0123456789abcdef0123456789abcdef"

    client = TelegramClient(session_name, dummy_id, dummy_hash)
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse('<div class="error">❌ Сессия недействительна или устарела</div>')

    clients[session_file.filename] = client
    return HTMLResponse('''
        <div class="success">✅ Успешный вход!</div>
        <a class="btn" href="/chats?offset=0">Показать чаты</a>
    ''')


@app.post("/login_manual")
async def login_manual(api_id: int = Form(...), api_hash: str = Form(...)):
    """Авторизация вручную"""
    session_path = os.path.join(SESSIONS_DIR, "manual_login")
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse('<div class="error">❌ Требуется ввести код из Telegram</div>')

    clients["manual_login"] = client
    return HTMLResponse('''
        <div class="success">✅ Вход выполнен вручную!</div>
        <a class="btn" href="/chats?offset=0">Показать чаты</a>
    ''')


@app.get("/chats", response_class=HTMLResponse)
async def get_chats(offset: int = Query(0, ge=0)):
    """Вывод чатов с аватарками и последними сообщениями"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    limit = 50  # Можно увеличить
    dialogs = await client.get_dialogs(limit=limit + offset)
    dialogs = dialogs[offset:offset + limit]

    html = """
    <html>
    <head>
        <link rel="stylesheet" href="/static/style.css">
        <title>Список чатов</title>
    </head>
    <body>
        <h1>Список чатов</h1>
        <div class="chat-container">
    """

    for dialog in dialogs:
        entity = dialog.entity
        title = dialog.name or "Без названия"
        last_message = dialog.message.message if dialog.message else "(пусто)"
        avatar_path = os.path.join(AVATAR_DIR, f"{entity.id}.jpg")
        avatar_url = f"/static/avatars/{entity.id}.jpg"

        # Загружаем фото, если его нет
        if not os.path.exists(avatar_path):
            try:
                await client.download_profile_photo(entity, file=avatar_path)
            except:
                avatar_url = "https://upload.wikimedia.org/wikipedia/commons/8/89/Portrait_Placeholder.png"

        html += f"""
        <div class="chat-card">
            <img src="{avatar_url}" class="chat-avatar">
            <div class="chat-info">
                <div class="chat-title">{title}</div>
                <div class="chat-last">{last_message[:80].replace('<', '&lt;')}</div>
                <div class="chat-id">ID: {entity.id}</div>
            </div>
        </div>
        """

    html += "</div>"

    html += "<div class='pagination'>"
    if offset > 0:
        html += f"<a class='btn' href='/chats?offset={max(offset - limit, 0)}'>&laquo; Назад</a>"
    if len(dialogs) == limit:
        html += f"<a class='btn' href='/chats?offset={offset + limit}'>Далее &raquo;</a>"
    html += "</div>"

    html += '<div class="back"><a href="/">↩ На главную</a></div>'
    html += "</body></html>"

    return HTMLResponse(html)
