from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
import os
import shutil

app = FastAPI()

# Папка с HTML шаблонами
templates = Jinja2Templates(directory="backend/templates")

# Папка для сохранения .session файлов
SESSIONS_DIR = "backend/sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Монтируем статику (если понадобится)
app.mount("/static", StaticFiles(directory="backend/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Отображает стартовую страницу с выбором способа входа:
    1) Через загрузку файла сессии
    2) Через ввод api_id и api_hash
    """
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/login_file")
async def login_with_file(session_file: UploadFile = File(...)):
    """
    Пользователь загружает свой .session файл
    """
    file_path = os.path.join(SESSIONS_DIR, session_file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(session_file.file, f)

    return {"status": "ok", "message": f"Файл {session_file.filename} загружен!"}


@app.post("/login_manual")
async def login_manual(api_id: str = Form(...), api_hash: str = Form(...)):
    """
    Пользователь вручную вводит api_id и api_hash
    """
    session_path = os.path.join(SESSIONS_DIR, f"manual_{api_id}.session")

    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return {"status": "error", "message": "Не авторизован. Нужна авторизация через код или QR."}

    me = await client.get_me()
    await client.disconnect()
    return {"status": "ok", "user": me.username if me.username else me.first_name}


@app.get("/list_chats")
async def list_chats():
    """
    Заглушка: позже сюда добавим код, который получит список чатов из Telethon
    """
    return {"status": "ok", "chats": []}
