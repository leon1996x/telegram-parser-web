from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telethon import TelegramClient
import os
import shutil
import asyncio

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)


def get_client(session_name: str, api_id: int, api_hash: str):
    session_path = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    return TelegramClient(session_path, api_id, api_hash)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/login_file")
async def login_file(request: Request, session_file: UploadFile = File(...)):
    # Сохраняем присланный .session файл
    session_path = os.path.join(SESSIONS_DIR, session_file.filename)
    with open(session_path, "wb") as f:
        shutil.copyfileobj(session_file.file, f)
    return RedirectResponse(f"/chats?session={session_file.filename}", status_code=302)


@app.post("/login_manual")
async def login_manual(
    request: Request,
    session_name: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
):
    client = get_client(session_name, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse(
            "<h3>Вы не авторизованы. Вход по коду пока не реализован в вебе.</h3>", status_code=401
        )

    await client.disconnect()
    return RedirectResponse(f"/chats?session={session_name}.session", status_code=302)


@app.get("/chats", response_class=HTMLResponse)
async def get_chats(request: Request, session: str):
    session_path = os.path.join(SESSIONS_DIR, session)

    if not os.path.exists(session_path):
        return HTMLResponse("<h3>Сессия не найдена</h3>", status_code=404)

    client = TelegramClient(session_path, 0, "")
    await client.connect()

    if not await client.is_user_authorized():
        return HTMLResponse("<h3>Сессия не авторизована</h3>", status_code=401)

    dialogs = await client.get_dialogs()
    chats = [d.name for d in dialogs]

    await client.disconnect()
    return templates.TemplateResponse("chats.html", {"request": request, "chats": chats})
