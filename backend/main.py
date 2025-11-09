import sys
import locale
import urllib.parse

# Принудительно устанавливаем UTF-8 кодировку
if sys.platform.startswith("win"):
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
else:
    locale.setlocale(locale.LC_ALL, 'C.UTF-8')

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None
sys.stderr.reconfigure(encoding='utf-8') if hasattr(sys.stderr, 'reconfigure') else None

from fastapi import FastAPI, Form, UploadFile, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat
import os
import shutil
from datetime import datetime, timedelta
import csv
import json
import zipfile
import io
import asyncio
import re

app = FastAPI()

# ПРАВИЛЬНЫЕ ПУТИ ДЛЯ RENDER
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
AVATAR_DIR = os.path.join(BASE_DIR, "static", "avatars")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
DATABASE_DIR = os.path.join(BASE_DIR, "database")
MEDIA_DIR = os.path.join(BASE_DIR, "static", "media")

# Создаем все необходимые папки
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)
os.makedirs(DATABASE_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)

# Создаем подпапки exports
for folder in ["chats", "csv", "participants", "archives"]:
    os.makedirs(os.path.join(EXPORTS_DIR, folder), exist_ok=True)

clients = {}

# Глобальное хранилище для поиска (в памяти)
search_index = {
    'keywords': {},      # слово -> {chat_id: [message_ids]}
    'users': {},         # user_id -> {username, first_name, etc}
    'messages': {},      # message_id -> message_data
    'chats': {}          # chat_id -> chat_info
}

# Middleware для принудительной UTF-8 кодировки
@app.middleware("http")
async def add_utf8_headers(request: Request, call_next):
    response = await call_next(request)
    # Добавляем UTF-8 заголовки ко всем ответам
    if "content-type" in response.headers and "charset" not in response.headers["content-type"].lower():
        if response.headers["content-type"].startswith("text/"):
            response.headers["content-type"] = response.headers["content-type"] + "; charset=utf-8"
    return response

def safe_filename(filename):
    """Создает безопасное имя файла без русских символов"""
    if not filename:
        return "chat"
    
    # Заменяем русские символы и специальные символы
    replacements = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        ' ': '_', '/': '_', '\\': '_', ':': '_', '*': '_', '?': '_', '"': '_',
        '<': '_', '>': '_', '|': '_'
    }
    
    filename = filename.lower()
    for rus, eng in replacements.items():
        filename = filename.replace(rus, eng)
    
    # Удаляем все остальные не-ASCII символы
    filename = ''.join(c if c.isalnum() or c in '._-' else '_' for c in filename)
    
    # Убеждаемся что имя файла не слишком длинное
    if len(filename) > 100:
        name, ext = os.path.splitext(filename)
        filename = name[:100-len(ext)] + ext
    
    return filename

def safe_error_message(error):
    """Создает безопасное сообщение об ошибке"""
    try:
        error_str = str(error)
        # Пробуем разные кодировки
        for encoding in ['utf-8', 'cp1251', 'latin-1']:
            try:
                return error_str.encode(encoding, errors='replace').decode(encoding)
            except:
                continue
        # Если все кодировки не сработали, возвращаем базовое сообщение
        return "Произошла ошибка при обработке данных"
    except:
        return "Неизвестная ошибка"

@app.exception_handler(Exception)
async def universal_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик всех исключений"""
    error_msg = safe_error_message(exc)
    return HTMLResponse(
        f'<div class="error">❌ Системная ошибка: {error_msg}</div>',
        status_code=500,
        headers={"Content-Type": "text/html; charset=utf-8"}
    )

@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open(os.path.join(TEMPLATES_DIR, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

@app.post("/login_file")
async def login_file(session_file: UploadFile):
    """Авторизация через .session файл"""
    try:
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
            <a class="btn" href="/admin" style="background:#8b5cf6;">🔍 Админка поиска</a>
        ''')
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

@app.post("/login_manual")
async def login_manual(api_id: int = Form(...), api_hash: str = Form(...)):
    """Авторизация вручную"""
    try:
        session_path = os.path.join(SESSIONS_DIR, "manual_login")
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            return HTMLResponse('<div class="error">❌ Требуется ввести код из Telegram</div>')

        clients["manual_login"] = client
        return HTMLResponse('''
            <div class="success">✅ Вход выполнен вручную!</div>
            <a class="btn" href="/chats?offset=0">Показать чаты</a>
            <a class="btn" href="/admin" style="background:#8b5cf6;">🔍 Админка поиска</a>
        ''')
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

async def get_chat_creation_date(client, entity):
    """Получает дату создания чата"""
    try:
        # Для каналов и супергрупп
        if hasattr(entity, 'date'):
            return entity.date
        # Для обычных групп и чатов
        elif hasattr(entity, 'participants_count'):
            # Пытаемся найти первое сообщение
            async for message in client.iter_messages(entity, limit=1, reverse=True):
                return message.date
        return None
    except:
        return None

def get_media_type(media):
    """Определяет тип медиа"""
    if hasattr(media, 'document'):
        if hasattr(media.document, 'mime_type'):
            mime = media.document.mime_type
            if mime and 'image' in mime:
                return "🖼️ Фото"
            elif mime and 'video' in mime:
                return "🎥 Видео"
            elif mime and 'audio' in mime:
                return "🎵 Аудио"
        return "📎 Файл"
    elif hasattr(media, 'photo'):
        return "🖼️ Фото"
    elif hasattr(media, 'webpage'):
        return "🌐 Ссылка"
    elif hasattr(media, 'sticker'):
        return "😊 Стикер"
    elif hasattr(media, 'contact'):
        return "👤 Контакт"
    elif hasattr(media, 'location'):
        return "📍 Локация"
    return "📎 Медиа"

async def download_media(client, message, chat_id):
    """Скачивает медиафайл и возвращает путь с улучшенной обработкой"""
    if not message.media:
        return None
    
    try:
        # Создаем папку для медиа чата
        chat_media_dir = os.path.join(MEDIA_DIR, str(chat_id))
        os.makedirs(chat_media_dir, exist_ok=True)
        
        # Получаем информацию о файле для правильного расширения
        file_ext = ".jpg"
        file_name = f"{message.id}"
        
        if hasattr(message.media, 'document'):
            doc = message.media.document
            if hasattr(doc, 'mime_type') and doc.mime_type:
                mime = doc.mime_type
                if 'image/jpeg' in mime or 'image/jpg' in mime:
                    file_ext = ".jpg"
                elif 'image/png' in mime:
                    file_ext = ".png"
                elif 'image/gif' in mime:
                    file_ext = ".gif"
                elif 'image/webp' in mime:
                    file_ext = ".webp"
                elif 'video/' in mime:
                    file_ext = ".mp4"
                elif 'audio/' in mime:
                    file_ext = ".mp3"
                elif 'application/pdf' in mime:
                    file_ext = ".pdf"
                else:
                    # Пытаемся получить расширение из attributes
                    for attr in doc.attributes:
                        if hasattr(attr, 'file_name') and attr.file_name:
                            file_name = attr.file_name
                            if '.' in file_name:
                                file_ext = '.' + file_name.split('.')[-1]
                            break
                    if file_ext == ".jpg":
                        file_ext = ".file"
            else:
                file_ext = ".file"
        elif hasattr(message.media, 'photo'):
            file_ext = ".jpg"
        elif hasattr(message.media, 'sticker'):
            file_ext = ".webp"
        
        filename = f"{file_name}{file_ext}"
        file_path = os.path.join(chat_media_dir, filename)
        
        # Скачиваем файл если его нет
        if not os.path.exists(file_path):
            print(f"📥 Скачиваю медиа: {filename}")
            try:
                # Быстрое скачивание с таймаутом
                await asyncio.wait_for(
                    client.download_media(message.media, file=file_path),
                    timeout=30.0
                )
                print(f"✅ Скачано: {filename}")
            except asyncio.TimeoutError:
                print(f"⏰ Таймаут при скачивании: {filename}")
                return None
            except Exception as download_error:
                print(f"❌ Ошибка скачивания {filename}: {safe_error_message(download_error)}")
                return None
        
        return f"/static/media/{chat_id}/{filename}"
    except Exception as e:
        print(f"❌ Ошибка загрузки медиа {message.id}: {safe_error_message(e)}")
        return None

async def download_media_fast(client, message, chat_id):
    """Быстрое скачивание медиа с кэшированием"""
    if not message.media:
        return None
    
    try:
        chat_media_dir = os.path.join(MEDIA_DIR, str(chat_id))
        os.makedirs(chat_media_dir, exist_ok=True)
        
        # Простое определение типа файла
        if hasattr(message.media, 'photo'):
            file_ext = ".jpg"
        elif hasattr(message.media, 'document'):
            doc = message.media.document
            if hasattr(doc, 'mime_type'):
                mime = doc.mime_type
                if mime and 'image' in mime:
                    file_ext = ".jpg"
                elif mime and 'video' in mime:
                    file_ext = ".mp4"
                elif mime and 'audio' in mime:
                    file_ext = ".mp3"
                else:
                    file_ext = ".file"
            else:
                file_ext = ".file"
        else:
            return None
        
        filename = f"{message.id}{file_ext}"
        file_path = os.path.join(chat_media_dir, filename)
        
        # Если файл уже существует, возвращаем путь
        if os.path.exists(file_path):
            return f"/static/media/{chat_id}/{filename}"
        
        # Быстрое скачивание без прогресса
        try:
            await asyncio.wait_for(
                client.download_media(message.media, file=file_path),
                timeout=15.0
            )
            return f"/static/media/{chat_id}/{filename}"
        except asyncio.TimeoutError:
            print(f"⏰ Таймаут при быстром скачивании: {filename}")
            return None
        
    except Exception as e:
        print(f"⚠️ Не удалось скачать медиа {message.id}: {safe_error_message(e)}")
        return None

async def get_chat_participants_from_list(client, entity):
    """Сбор участников ТОЛЬКО из списка участников (БЕЗ ЛИМИТА)"""
    participants = {}
    
    print(f"🔍 Сбор участников ИЗ СПИСКА для: {getattr(entity, 'title', 'чата')}")
    
    try:
        if hasattr(entity, 'participants_count'):
            print(f"👥 Получаем участников из списка (всего в чате: {entity.participants_count})...")
            added_count = 0
            
            # БЕЗ ЛИМИТА - собираем всех участников
            async for user in client.iter_participants(entity):
                if user.id not in participants:
                    participants[user.id] = {
                        'id': user.id,
                        'username': user.username or '',
                        'first_name': user.first_name or '',
                        'last_name': user.last_name or '',
                        'phone': user.phone or '',
                        'source': 'participants_list_only'
                    }
                    added_count += 1
                    
                    # Прогресс каждые 1000 участников
                    if added_count % 1000 == 0:
                        print(f"👥 Собрано {added_count} участников из списка...")
            
            print(f"✅ Добавлено {added_count} участников из списка")
        else:
            print("⚠️ Это не группа/канал, невозможно получить список участников")
            return {}
            
    except Exception as e:
        print(f"❌ Не удалось получить список участников: {safe_error_message(e)}")
        return {}
    
    return participants

async def get_chat_participants_guaranteed(client, entity, limit=50000):
    """ГАРАНТИРОВАННЫЙ сбор участников ТОЛЬКО из сообщений"""
    participants = {}
    message_count = 0
    
    print(f"🔍 ГАРАНТИРОВАННЫЙ сбор участников ИЗ СООБЩЕНИЙ для: {getattr(entity, 'title', 'чата')}")
    print(f"📝 Читаем до {limit} сообщений...")
    
    try:
        # Читаем ВСЕ сообщения подряд
        async for message in client.iter_messages(entity, limit=limit):
            message_count += 1
            
            # ОСНОВНОЙ СПОСОБ - sender_id который ВСЕГДА есть
            if hasattr(message, 'sender_id') and message.sender_id:
                sender_id = message.sender_id
                
                if sender_id not in participants:
                    # Базовая информация
                    participant_data = {
                        'id': sender_id,
                        'username': '',
                        'first_name': '',
                        'last_name': '',
                        'phone': '',
                        'source': 'messages_only'
                    }
                    
                    # Дополнительная информация если доступна
                    try:
                        if message.sender:
                            participant_data['username'] = getattr(message.sender, 'username', '') or ''
                            participant_data['first_name'] = getattr(message.sender, 'first_name', '') or ''
                            participant_data['last_name'] = getattr(message.sender, 'last_name', '') or ''
                            participant_data['phone'] = getattr(message.sender, 'phone', '') or ''
                    except:
                        pass
                    
                    participants[sender_id] = participant_data
            
            # Прогресс каждые 1000 сообщений
            if message_count % 1000 == 0:
                print(f"📨 Прочитано {message_count} сообщений, найдено {len(participants)} участников")
                
    except Exception as e:
        print(f"❌ Ошибка при чтении сообщений: {safe_error_message(e)}")
    
    print(f"✅ ГАРАНТИРОВАННЫЙ сбор ИЗ СООБЩЕНИЙ завершен! Прочитано {message_count} сообщений, собрано {len(participants)} участников")
    
    return participants

async def get_chat_participants_fast(client, entity, limit=500):
    """Быстрая версия для интерфейса"""
    participants = {}
    
    # Только последние сообщения для быстрого показа
    async for message in client.iter_messages(entity, limit=limit):
        if hasattr(message, 'sender_id') and message.sender_id:
            sender_id = message.sender_id
            if sender_id not in participants:
                participants[sender_id] = {
                    'id': sender_id,
                    'username': getattr(message.sender, 'username', '') if message.sender else '',
                    'first_name': getattr(message.sender, 'first_name', '') if message.sender else '',
                    'last_name': getattr(message.sender, 'last_name', '') if message.sender else '',
                    'source': 'fast'
                }
    
    return participants

def get_chat_link(entity):
    """Генерирует ссылку на чат"""
    try:
        if hasattr(entity, 'username') and entity.username:
            return f"https://t.me/{entity.username}"
        elif hasattr(entity, 'id'):
            return f"https://t.me/c/{str(entity.id).replace('-100', '')}"
        else:
            return "Ссылка недоступна"
    except:
        return "Ссылка недоступна"

@app.get("/chats", response_class=HTMLResponse)
async def get_chats(offset: int = Query(0, ge=0)):
    """Вывод чатов с аватарками и последними сообщениями"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    limit = 50
    dialogs = await client.get_dialogs(limit=limit + offset)
    dialogs = dialogs[offset:offset + limit]

    html = """
    <html>
    <head>
        <link rel="stylesheet" href="/static/style.css">
        <title>Список чатов</title>
    </head>
    <body>
        <h1 style="text-align:center; margin-bottom:20px;">💬 Список чатов</h1>
        
        <div style="text-align:center; margin:20px;">
            <a class="btn" href="/export_all" style="background:#10b981;">📁 Экспорт всех чатов (T3)</a>
            <a class="btn" href="/admin" style="background:#8b5cf6;">🔍 Админка поиска</a>
        </div>
        
        <div class="chat-container">
    """

    for dialog in dialogs:
        entity = dialog.entity
        title = dialog.name or "Без названия"

        # Получаем дату создания чата
        creation_date = await get_chat_creation_date(client, entity)
        creation_date_str = creation_date.strftime("%d.%m.%Y") if creation_date else "Неизвестно"

        # Получаем ссылку на чат
        chat_link = get_chat_link(entity)

        # Получаем количество участников
        participants_count = "Неизвестно"
        if hasattr(entity, 'participants_count'):
            participants_count = entity.participants_count

        # Безопасное извлечение последнего сообщения
        if dialog.message and getattr(dialog.message, "message", None):
            last_message = dialog.message.message
        else:
            media_type = getattr(dialog.message, "media", None)
            if media_type:
                last_message = f"[{get_media_type(media_type)}]"
            else:
                last_message = "(нет сообщений)"

        # Получаем время сообщения с датой
        if dialog.message and hasattr(dialog.message, 'date'):
            message_date = dialog.message.date
            message_time = message_date.strftime("%d.%m.%Y %H:%M")
        else:
            message_time = "--:--"

        avatar_path = os.path.join(AVATAR_DIR, f"{entity.id}.jpg")
        avatar_url = f"/static/avatars/{entity.id}.jpg"

        # Загружаем фото, если его нет
        if not os.path.exists(avatar_path):
            try:
                await client.download_profile_photo(entity, file=avatar_path)
            except:
                avatar_url = "https://upload.wikimedia.org/wikipedia/commons/8/89/Portrait_Placeholder.png"

        # Экранируем HTML символы в сообщении
        safe_message = str(last_message).replace('<', '&lt;').replace('>', '&gt;')[:90]

        html += f"""
        <div class="chat-card">
            <img src="{avatar_url}" class="chat-avatar" onerror="this.src='https://upload.wikimedia.org/wikipedia/commons/8/89/Portrait_Placeholder.png'">
            <div class="chat-info">
                <div class="chat-title-row">
                    <div class="chat-title">{title}</div>
                    <div class="chat-actions">
                        <a class="btn-view" href="/chat/{entity.id}">👁️ Просмотр</a>
                        <a class="btn-download" href="/download_participants/{entity.id}?format=html&source=list">👥 Участники</a>
                        <a class="btn-download" href="/force_collect/{entity.id}" style="background:#ef4444;">🚀 Принудительный сбор</a>
                    </div>
                </div>
                <div class="chat-meta">
                    <span class="chat-creation">📅 Создан: {creation_date_str}</span>
                    <span class="chat-link">🔗 <a href="{chat_link}" target="_blank">Ссылка на чат</a></span>
                    <span class="chat-participants">👥 Участников: {participants_count}</span>
                    <span class="chat-last-msg">💬 {safe_message}</span>
                </div>
                <div class="chat-time">{message_time}</div>
                <div class="chat-id">ID: {entity.id}</div>
            </div>
        </div>
        """

    html += "</div>"

    # Навигация
    html += "<div class='pagination'>"
    if offset > 0:
        html += f"<a class='btn' href='/chats?offset={max(offset - limit, 0)}'>&laquo; Назад</a>"
    if len(dialogs) == limit:
        html += f"<a class='btn' href='/chats?offset={offset + limit}'>Далее &raquo;</a>"
    html += "</div>"

    html += '<div class="back"><a href="/">↩ Назад к чатам</a></div>'
    html += "</body></html>"

    return HTMLResponse(html)

def create_download_buttons(chat_id, chat_title):
    """Создает умные кнопки скачивания с предустановленными периодами"""
    
    today = datetime.now()
    periods = [
        {"name": "📅 Последние 7 дней", "days": 7},
        {"name": "📅 Последние 30 дней", "days": 30},
        {"name": "📅 Последние 90 дней", "days": 90},
        {"name": "📅 Этот месяц", "days": "month"},
        {"name": "📅 Прошлый месяц", "days": "last_month"},
        {"name": "🚀 ВСЯ ИСТОРИЯ", "days": "all"}
    ]
    
    buttons_html = """
    <div class="download-section">
        <h3>📥 Скачать историю с медиафайлами:</h3>
        <div class="progress-container" id="progressContainer" style="display: none;">
            <div class="progress-bar">
                <div class="progress-fill" id="progressFill"></div>
            </div>
            <div class="progress-text" id="progressText">Подготовка...</div>
        </div>
        <div class="period-buttons">
    """
    
    for period in periods:
        buttons_html += f"""
            <div class="period-group">
                <div class="period-name">{period['name']}</div>
                <div class="format-buttons">
                    <a class="btn-download small" href="/download_period/{chat_id}?days={period['days']}&format=html" onclick="showProgress('{period['name']}')">HTML</a>
                    <a class="btn-download small" href="/download_period/{chat_id}?days={period['days']}&format=txt" onclick="showProgress('{period['name']}')">TXT</a>
                    <a class="btn-download small" href="/download_period/{chat_id}?days={period['days']}&format=csv" onclick="showProgress('{period['name']}')">CSV</a>
                </div>
            </div>
        """
    
    buttons_html += f"""
        </div>
        
        <div class="fast-download">
            <h4>⚡ Быстрая версия (без медиа):</h4>
            <div class="format-buttons">
                <a class="btn-download small" href="/download_period_fast/{chat_id}?days=all&format=html" onclick="showProgress('Вся история (быстро)')">HTML</a>
                <a class="btn-download small" href="/download_period_fast/{chat_id}?days=all&format=txt" onclick="showProgress('Вся история (быстро)')">TXT</a>
                <a class="btn-download small" href="/download_period_fast/{chat_id}?days=all&format=csv" onclick="showProgress('Вся история (быстро)')">CSV</a>
            </div>
        </div>
        
        <div class="participants-download">
            <h4>👥 Скачать список участников:</h4>
            <div class="format-buttons">
                <a class="btn-download small" href="/download_participants/{chat_id}?format=html&source=list" style="background:#3b82f6;">📋 ИЗ СПИСКА УЧАСТНИКОВ</a>
                <a class="btn-download small" href="/download_participants/{chat_id}?format=json&source=list">JSON</a>
                <a class="btn-download small" href="/download_participants/{chat_id}?format=csv&source=list">CSV</a>
                <a class="btn-download small" href="/download_participants/{chat_id}?format=txt&source=list">TXT</a>
            </div>
            <p style="color:#666; font-size:12px; margin-top:8px;">
                <strong>📋 ИЗ СПИСКА УЧАСТНИКОВ</strong> - официальный список участников группы/канала (ВСЕ участники)
            </p>
        </div>
        
        <p class="note">💡 Фото, видео и аудио будут встроены в HTML файл</p>
        <p class="note">⚡ Для больших периодов скачивание может занять несколько минут</p>
    </div>
    
    <script>
    function showProgress(periodName) {{
        document.getElementById('progressContainer').style.display = 'block';
        document.getElementById('progressText').textContent = 'Подготовка ' + periodName + '...';
        
        let progress = 0;
        const interval = setInterval(() => {{
            progress += Math.random() * 10;
            if (progress > 90) progress = 90;
            document.getElementById('progressFill').style.width = progress + '%';
            document.getElementById('progressText').textContent = 'Обработка ' + periodName + '... ' + Math.round(progress) + '%';
        }}, 500);
        
        // Остановить анимацию когда страница загрузится
        window.addEventListener('beforeunload', () => {{
            clearInterval(interval);
        }});
    }}
    </script>
    """
    
    return buttons_html

@app.get("/chat/{chat_id}")
async def view_chat(chat_id: int, offset_id: int = Query(0, ge=0), highlight: str = None):
    """Детальная страница чата - БЫСТРАЯ загрузка"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    limit = 50  # сообщений на страницу

    try:
        # Пробуем получить entity разными способами
        try:
            entity = await client.get_entity(chat_id)
        except ValueError:
            try:
                entity = await client.get_entity(PeerChannel(chat_id))
            except:
                try:
                    entity = await client.get_entity(PeerChat(chat_id))
                except:
                    return HTMLResponse('<div class="error">❌ Чат не найден</div>')
        
        chat_title = getattr(entity, 'title', 'Личная переписка')
        creation_date = await get_chat_creation_date(client, entity)
        chat_link = get_chat_link(entity)
        
        # АВТОМАТИЧЕСКАЯ ИНДЕКСАЦИЯ ПРИ ПЕРВОМ ПРОСМОТРЕ
        if chat_id not in search_index['chats']:
            asyncio.create_task(index_chat_on_view(client, entity))
        
        # БЫСТРЫЙ сбор участников - только последние 500 сообщений
        participants = await get_chat_participants_fast(client, entity, limit=500)
        participants_count = len(participants)
        
        # Получаем сообщения
        messages = []
        async for message in client.iter_messages(entity, limit=limit, offset_id=offset_id):
            messages.append(message)
        
        # Находим ID для пагинации
        next_offset_id = messages[-1].id - 1 if messages else 0
        prev_offset_id = messages[0].id + limit if messages else 0
        
        messages_html = ""
        for message in messages:
            if message.out:
                sender_type = "Исходящее"
                sender_info = f"Вы (ID {message.sender_id})"
            else:
                sender = message.sender
                if sender:
                    username = f"@{sender.username}" if sender.username else ""
                    sender_info = f"{username} (ID {sender.id})"
                else:
                    sender_info = f"Unknown (ID {message.sender_id})"
                sender_type = "Входящее"
            
            # Обрабатываем контент сообщения
            content = ""
            if message.text:
                content = message.text
                # Подсветка если есть параметр highlight
                if highlight and highlight in content.lower():
                    content = highlight_text(content, highlight)
            elif message.media:
                media_type = get_media_type(message.media)
                media_url = await download_media_fast(client, message, chat_id)
                if media_url:
                    if "Фото" in media_type:
                        content = f'🖼️ <a href="{media_url}" target="_blank"><img src="{media_url}" class="media-preview" alt="Фото"></a>'
                    elif "Видео" in media_type:
                        content = f'🎥 <a href="{media_url}" target="_blank">Видео файл</a>'
                    elif "Аудио" in media_type:
                        content = f'🎵 <a href="{media_url}" target="_blank">Аудио файл</a>'
                    elif "Стикер" in media_type:
                        content = f'😊 <a href="{media_url}" target="_blank">Стикер</a>'
                    else:
                        content = f'📎 <a href="{media_url}" target="_blank">{media_type}</a>'
                else:
                    content = f"[{media_type}]"
            else:
                content = "[Пустое сообщение]"
            
            messages_html += f"""
            <div class="message {'outgoing' if message.out else 'incoming'}">
                <div class="message-header">
                    <strong>{sender_type}: {sender_info}</strong>
                    <span class="message-time">{message.date.strftime('%d.%m.%Y %H:%M:%S')}</span>
                </div>
                <div class="message-content">{content}</div>
            </div>
            """

        # 📅 УМНЫЕ КНОПКИ СКАЧИВАНИЯ С ПЕРИОДАМИ
        download_buttons = create_download_buttons(chat_id, chat_title)
        
        # Информация об участниках
        participants_from_list = sum(1 for p in participants.values() if p['source'] == 'participants_list')
        participants_from_messages = sum(1 for p in participants.values() if p['source'] == 'from_messages')
        
        participants_info = f"""
        <div class="participants-info">
            <h3>👥 Информация об участниках (быстрый сбор):</h3>
            <div class="participants-stats">
                <p><strong>Всего участников:</strong> {participants_count}</p>
                <p><strong>Из списка участников:</strong> {participants_from_list}</p>
                <p><strong>Из истории сообщений:</strong> {participants_from_messages}</p>
                <p><strong>С @username:</strong> {sum(1 for p in participants.values() if p['username'])}</p>
                <p><em>💡 Для полного списка используйте кнопку "Принудительный сбор"</em></p>
            </div>
        </div>
        """
        
        html = f"""
        <html>
        <head>
            <link rel="stylesheet" href="/static/style.css">
            <title>Чат: {chat_title}</title>
        </head>
        <body>
            <div class="chat-header">
                <h1>💬 {chat_title}</h1>
                <div class="chat-info-bar">
                    <span>📅 Создан: {creation_date.strftime('%d.%m.%Y') if creation_date else 'Неизвестно'}</span>
                    <span>🆔 ID: {chat_id}</span>
                    <span>💬 Сообщений: {len(messages)}</span>
                    <span>🔗 <a href="{chat_link}" target="_blank">Ссылка на чат</a></span>
                </div>
                
                {participants_info}
                {download_buttons}
                
                <div style="text-align:center; margin:20px;">
                    <a class="btn" href="/force_collect/{chat_id}" style="background:#ef4444; font-size:18px; padding:15px 30px;">🚀 ЗАПУСТИТЬ ПРИНУДИТЕЛЬНЫЙ СБОР УЧАСТНИКОВ</a>
                    <p style="color:#666; margin-top:10px;">Эта операция прочитает ВСЕ сообщения в чате и гарантированно соберет всех участников</p>
                </div>
                
                <div class="custom-period">
                    <h4>📅 Или укажите свой период:</h4>
                    <form action="/download_custom_period/{chat_id}" method="get" class="period-form">
                        <div class="date-inputs">
                            <label>С:</label>
                            <input type="date" name="start_date" required>
                            <label>По:</label>
                            <input type="date" name="end_date" required>
                        </div>
                        <div class="format-buttons">
                            <button type="submit" name="format" value="html" class="btn-download">📥 HTML</button>
                            <button type="submit" name="format" value="txt" class="btn-download">📄 TXT</button>
                            <button type="submit" name="format" value="csv" class="btn-download">📊 CSV</button>
                        </div>
                    </form>
                </div>
                
                <a class="btn" href="/chats">← Назад к чатам</a>
                <a class="btn" href="/admin" style="background:#8b5cf6;">🔍 Админка поиска</a>
            </div>
            
            <div class="messages-container">
                {messages_html}
            </div>
            
            <div class="pagination">
        """
        
        if messages:
            html += f"<a class='btn' href='/chat/{chat_id}?offset_id={next_offset_id}'>⏩ Более старые</a>"
        
        if offset_id > 0:
            html += f"<a class='btn' href='/chat/{chat_id}?offset_id={max(offset_id - limit * 2, 0)}'>⏪ Более новые</a>"
        
        html += "</div></body></html>"
        
        return HTMLResponse(html)
        
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

async def index_chat_on_view(client, entity):
    """Автоматическая индексация чата при просмотре"""
    try:
        chat_id = entity.id
        chat_title = getattr(entity, 'title', 'Личная переписка')
        
        print(f"🔍 Автоматическая индексация чата: {chat_title}")
        
        # Индексируем только последние 200 сообщений для скорости
        async for message in client.iter_messages(entity, limit=200):
            await index_message(message, chat_id)
            
    except Exception as e:
        print(f"❌ Ошибка автоматической индексации: {safe_error_message(e)}")

@app.get("/force_collect/{chat_id}")
async def force_collect_participants(chat_id: int):
    """ПРИНУДИТЕЛЬНЫЙ сбор участников - читает ВСЕ сообщения"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    
    try:
        # Пробуем получить entity разными способами
        try:
            entity = await client.get_entity(chat_id)
        except ValueError:
            try:
                entity = await client.get_entity(PeerChannel(chat_id))
            except:
                try:
                    entity = await client.get_entity(PeerChat(chat_id))
                except:
                    return HTMLResponse('<div class="error">❌ Чат не найден</div>')
        
        chat_title = getattr(entity, 'title', 'Личная переписка')
        
        print(f"🚀 ЗАПУСК ПРИНУДИТЕЛЬНОГО СБОРА ИЗ СООБЩЕНИЙ ДЛЯ: {chat_title}")
        
        # Принудительный сбор ТОЛЬКО из сообщений
        participants = await get_chat_participants_guaranteed(client, entity, limit=100000)
        
        # Сохраняем результат
        result = {
            'chat_title': chat_title,
            'chat_id': chat_id,
            'total_participants': len(participants),
            'participants_count_with_username': sum(1 for p in participants.values() if p['username']),
            'participants': list(participants.values()),
            'collected_at': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
            'source': 'messages_only'
        }
        
        # Показываем подробный результат
        participants_list = list(participants.values())
        participants_list.sort(key=lambda x: x['id'])
        
        result_html = f"""
        <html>
        <head>
            <link rel="stylesheet" href="/static/style.css">
            <title>Результат ПРИНУДИТЕЛЬНОГО сбора: {chat_title}</title>
        </head>
        <body>
            <div class="chat-header">
                <h1>🎯 Результат ПРИНУДИТЕЛЬНОГО сбора (ИЗ СООБЩЕНИЙ)</h1>
                <div class="success" style="text-align:center; padding:20px;">
                    <h3>Чат: {chat_title}</h3>
                    <p><strong>✅ Собрано участников ИЗ СООБЩЕНИЙ:</strong> {len(participants)}</p>
                    <p><strong>📊 С @username:</strong> {sum(1 for p in participants.values() if p['username'])}</p>
                    <p><strong>🕒 Время сбора:</strong> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>
                    <p><strong>📝 Источник:</strong> ТОЛЬКО из истории сообщений</p>
                </div>
                
                <div style="text-align:center; margin:20px;">
                    <a class="btn" href="/download_participants/{chat_id}?format=html&source=messages" style="background:#10b981; font-size:18px; padding:15px 30px;">📥 Скачать список участников ИЗ СООБЩЕНИЙ</a>
                    <a class="btn" href="/chat/{chat_id}">← Назад к чату</a>
                </div>
                
                <h3>📋 Первые 100 участников (из сообщений):</h3>
                <div style="overflow-x:auto;">
                    <table style="width:100%; border-collapse: collapse;">
                        <thead>
                            <tr style="background:#ef4444; color:white;">
                                <th style="padding:10px; border:1px solid #ddd;">ID</th>
                                <th style="padding:10px; border:1px solid #ddd;">Username</th>
                                <th style="padding:10px; border:1px solid #ddd;">Имя</th>
                                <th style="padding:10px; border:1px solid #ddd;">Фамилия</th>
                                <th style="padding:10px; border:1px solid #ddd;">Источник</th>
                            </tr>
                        </thead>
                        <tbody>
        """
        
        for participant in participants_list[:100]:
            result_html += f"""
                            <tr>
                                <td style="padding:8px; border:1px solid #ddd;">{participant['id']}</td>
                                <td style="padding:8px; border:1px solid #ddd;">@{participant['username']}</td>
                                <td style="padding:8px; border:1px solid #ddd;">{participant['first_name']}</td>
                                <td style="padding:8px; border:1px solid #ddd;">{participant['last_name']}</td>
                                <td style="padding:8px; border:1px solid #ddd;">{participant['source']}</td>
                            </tr>
            """
        
        result_html += f"""
                        </tbody>
                    </table>
                </div>
                <p style="text-align:center; margin-top:20px; color:#666;">
                    <em>Показано первых 100 из {len(participants)} участников. Полный список доступен для скачивания.</em>
                </p>
                <p style="text-align:center; color:#ef4444; font-weight:bold;">
                    ⚠️ ВНИМАНИЕ: Это участники ТОЛЬКО из истории сообщений!
                </p>
            </div>
        </body>
        </html>
        """
        
        return HTMLResponse(result_html)
        
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

@app.get("/download_participants/{chat_id}")
async def download_participants(chat_id: int, format: str = "html", source: str = "list"):
    """Скачать список участников чата"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    
    try:
        # Пробуем получить entity разными способами
        try:
            entity = await client.get_entity(chat_id)
        except ValueError:
            try:
                entity = await client.get_entity(PeerChannel(chat_id))
            except:
                try:
                    entity = await client.get_entity(PeerChat(chat_id))
                except:
                    return HTMLResponse('<div class="error">❌ Чат не найден</div>')
        
        chat_title = getattr(entity, 'title', 'Личная переписка')
        chat_link = get_chat_link(entity)
        
        # Выбираем метод сбора в зависимости от параметра source
        if source == "messages":
            print(f"📥 Сбор участников ИЗ СООБЩЕНИЙ для: {chat_title}")
            participants = await get_chat_participants_guaranteed(client, entity, limit=50000)
            source_name = "из сообщений"
            source_color = "#ef4444"
        else:
            print(f"📥 Сбор участников ИЗ СПИСКА для: {chat_title}")
            # БЕЗ ЛИМИТА - собираем ВСЕХ участников
            participants = await get_chat_participants_from_list(client, entity)
            source_name = "из списка участников"
            source_color = "#3b82f6"
        
        if not participants:
            return HTMLResponse(f'<div class="error">❌ Не удалось собрать участников {source_name}</div>')
        
        # Создаем безопасное имя файла
        safe_chat_title = safe_filename(chat_title) or f"chat_{chat_id}"
        
        if format == "html":
            content = generate_participants_html(chat_title, chat_link, participants, source_name, source_color)
            filename = f"{safe_chat_title}_participants_{source}.html"
            media_type = "text/html; charset=utf-8"
        elif format == "json":
            content = generate_participants_json(participants, source_name)
            filename = f"{safe_chat_title}_participants_{source}.json"
            media_type = "application/json; charset=utf-8"
        elif format == "csv":
            content = generate_participants_csv(participants, source_name)
            filename = f"{safe_chat_title}_participants_{source}.csv"
            media_type = "text/csv; charset=utf-8"
        else:  # txt
            content = generate_participants_txt(chat_title, chat_link, participants, source_name)
            filename = f"{safe_chat_title}_participants_{source}.txt"
            media_type = "text/plain; charset=utf-8"
        
        # Кодируем имя файла для безопасной загрузки
        encoded_filename = urllib.parse.quote(filename)
        
        return HTMLResponse(
            content,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
                "Content-Type": media_type
            }
        )
        
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

def generate_participants_html(chat_title, chat_link, participants, source_name, source_color="#3b82f6"):
    """Генерирует HTML файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    participants_html = ""
    for participant in participants_list:
        username = f"@{participant['username']}" if participant['username'] else "нет username"
        full_name = f"{participant['first_name']} {participant['last_name']}".strip()
        if not full_name:
            full_name = "Не указано"
        
        participants_html += f"""
        <tr>
            <td>{participant['id']}</td>
            <td>{username}</td>
            <td>{full_name}</td>
            <td>{participant['phone'] or 'Не указан'}</td>
            <td>{participant['source']}</td>
        </tr>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Участники чата: {chat_title}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .header {{ background: white; padding: 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .stats {{ background: {source_color}; color: white; padding: 12px; border-radius: 8px; margin: 15px 0; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: {source_color}; color: white; }}
            tr:hover {{ background: #f5f5f5; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>👥 Участники чата: {chat_title}</h1>
            <div class="stats">
                <strong>📊 Статистика:</strong> {len(participants)} участников | <strong>Источник:</strong> {source_name}
            </div>
            <p><strong>🔗 Ссылка на чат:</strong> <a href="{chat_link}" target="_blank">{chat_link}</a></p>
            <p><strong>📤 Экспорт:</strong> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>ID Telegram</th>
                    <th>@username</th>
                    <th>Имя и фамилия</th>
                    <th>Телефон</th>
                    <th>Источник</th>
                </tr>
            </thead>
            <tbody>
                {participants_html}
            </tbody>
        </table>
    </body>
    </html>
    """

def generate_participants_json(participants, source_name):
    """Генерирует JSON файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    return json.dumps({
        'export_date': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'total_participants': len(participants_list),
        'source': source_name,
        'participants': participants_list
    }, ensure_ascii=False, indent=2)

def generate_participants_csv(participants, source_name):
    """Генерирует CSV файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['ID Telegram', '@username', 'Имя', 'Фамилия', 'Телефон', 'Источник'])
    
    for participant in participants_list:
        writer.writerow([
            participant['id'],
            participant['username'] or '',
            participant['first_name'] or '',
            participant['last_name'] or '',
            participant['phone'] or '',
            participant['source']
        ])
    
    return output.getvalue()

def generate_participants_txt(chat_title, chat_link, participants, source_name):
    """Генерирует TXT файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    content = f"Участники чата: {chat_title}\n"
    content += f"Ссылка: {chat_link}\n"
    content += f"Всего участников: {len(participants_list)}\n"
    content += f"Источник: {source_name}\n"
    content += f"Экспорт: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
    content += "="*80 + "\n\n"
    
    for participant in participants_list:
        username = f"@{participant['username']}" if participant['username'] else "нет username"
        full_name = f"{participant['first_name']} {participant['last_name']}".strip()
        if not full_name:
            full_name = "Не указано"
        
        content += f"ID: {participant['id']}\n"
        content += f"Username: {username}\n"
        content += f"Имя: {full_name}\n"
        content += f"Телефон: {participant['phone'] or 'Не указан'}\n"
        content += f"Источник: {participant['source']}\n"
        content += "-" * 40 + "\n"
    
    return content

# ДОБАВЛЯЕМ ФУНКЦИИ ПОИСКА

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    """Админка с поиском"""
    return """
    <html>
    <head>
        <link rel="stylesheet" href="/static/style.css">
        <title>Админка - Поиск</title>
        <style>
            .search-section { background: white; padding: 25px; margin: 20px auto; max-width: 800px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .search-form { display: flex; flex-direction: column; gap: 15px; }
            .search-input { padding: 12px; border: 2px solid #e2e8f0; border-radius: 8px; font-size: 16px; }
            .search-type { display: flex; gap: 15px; margin: 10px 0; flex-wrap: wrap; }
            .search-type label { display: flex; align-items: center; gap: 5px; padding: 8px 12px; background: #f8fafc; border-radius: 6px; }
            .results { margin-top: 30px; }
            .result-item { background: #f8fafc; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #3b82f6; }
            .result-chat { font-weight: bold; color: #1a202c; font-size: 16px; }
            .result-message { color: #4a5568; margin: 5px 0; line-height: 1.4; }
            .result-meta { color: #718096; font-size: 12px; margin-top: 5px; }
            .highlight { background: yellow; padding: 2px; border-radius: 2px; font-weight: bold; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
            .stat-card { background: white; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #e2e8f0; }
            .stat-number { font-size: 24px; font-weight: bold; color: #3b82f6; }
        </style>
    </head>
    <body>
        <div style="text-align:center; padding:20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
            <h1>🔍 Админка - Поиск по всем данным</h1>
            <p>Поиск по ключевым словам и пользователям во всех загруженных чатах</p>
        </div>

        <div class="search-section">
            <h2>🔎 Поиск по ключевым словам</h2>
            <form action="/admin/search" method="get" class="search-form">
                <input type="text" name="query" class="search-input" placeholder="Введите ключевое слово или фразу..." required>
                <div class="search-type">
                    <label><input type="radio" name="type" value="keyword" checked> 🔤 По ключевым словам</label>
                    <label><input type="radio" name="type" value="user"> 👤 По пользователям (ID/Username/Имя)</label>
                </div>
                <button type="submit" class="btn" style="padding:12px; font-size:16px; background:#8b5cf6;">🔍 Найти</button>
            </form>
        </div>

        <div class="search-section">
            <h2>📊 Статистика индекса поиска</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number" id="wordCount">0</div>
                    <div>🔤 Проиндексировано слов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="userCount">0</div>
                    <div>👥 Проиндексировано пользователей</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="messageCount">0</div>
                    <div>💬 Проиндексировано сообщений</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="chatCount">0</div>
                    <div>💬 Проиндексировано чатов</div>
                </div>
            </div>
            <div style="text-align:center; margin-top:20px;">
                <button class="btn" onclick="updateIndex()" style="background:#10b981;">🔄 Обновить индекс поиска</button>
                <button class="btn" onclick="clearIndex()" style="background:#ef4444;">🗑️ Очистить индекс</button>
            </div>
        </div>

        <div style="text-align:center; margin:30px;">
            <a class="btn" href="/chats">💬 К списку чатов</a>
            <a class="btn" href="/">🏠 На главную</a>
        </div>

        <script>
            function updateIndex() {
                fetch('/admin/update_index')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('wordCount').textContent = data.words || 0;
                        document.getElementById('userCount').textContent = data.users || 0;
                        document.getElementById('messageCount').textContent = data.messages || 0;
                        document.getElementById('chatCount').textContent = data.chats || 0;
                        alert('✅ Индекс обновлен!\\nСлов: ' + data.words + '\\nПользователей: ' + data.users + '\\nСообщений: ' + data.messages + '\\nЧатов: ' + data.chats);
                    })
                    .catch(err => {
                        alert('❌ Ошибка обновления индекса');
                    });
            }

            function clearIndex() {
                if(confirm('Очистить весь индекс поиска? Это удалит все проиндексированные данные.')) {
                    fetch('/admin/clear_index')
                        .then(() => {
                            updateIndex();
                            alert('✅ Индекс очищен!');
                        })
                        .catch(err => {
                            alert('❌ Ошибка очистки индекса');
                        });
                }
            }

            // Загружаем статистику при загрузке страницы
            updateIndex();
        </script>
    </body>
    </html>
    """

@app.get("/admin/search", response_class=HTMLResponse)
async def admin_search(query: str, type: str = "keyword"):
    """Поиск по ключевым словам или пользователям"""
    if not query.strip():
        return HTMLResponse('<div class="error">❌ Введите поисковый запрос</div>')

    results_html = ""
    
    if type == "keyword":
        # Поиск по ключевым словам
        results = await search_keywords(query.lower())
        if not results:
            return HTMLResponse(f'''
                <div class="error">❌ По запросу "{query}" ничего не найдено</div>
                <div style="text-align:center; margin:20px;">
                    <a class="btn" href="/admin">🔍 Новый поиск</a>
                    <a class="btn" href="/admin/update_index">🔄 Обновить индекс</a>
                </div>
            ''')
        
        for chat_id, messages in results.items():
            chat_info = search_index['chats'].get(chat_id, {'title': f'Чат {chat_id}'})
            chat_title = chat_info.get('title', f'Чат {chat_id}')
            chat_link = chat_info.get('link', '#')
            
            results_html += f'<div class="result-item">'
            results_html += f'<div class="result-chat">💬 <a href="{chat_link}" target="_blank">{chat_title}</a> (ID: {chat_id})</div>'
            results_html += f'<div class="result-meta">📊 Найдено сообщений: {len(messages)}</div>'
            
            for msg_data in messages[:5]:  # Показываем первые 5 сообщений
                message = msg_data.get('message', '')
                # Подсветка найденных слов
                highlighted_message = highlight_text(message, query)
                results_html += f'''
                <div class="result-message">{highlighted_message}</div>
                <div class="result-meta">
                    📅 {msg_data.get('date', '')} | 
                    👤 {msg_data.get('sender', 'Unknown')} |
                    🔗 <a href="/chat/{chat_id}?highlight={query}">Перейти к чату</a>
                </div>
                <hr style="margin:10px 0; border:none; border-top:1px solid #e2e8f0;">
                '''
            
            if len(messages) > 5:
                results_html += f'<div class="result-meta">... и еще {len(messages) - 5} сообщений</div>'
            
            results_html += '</div>'
    
    else:
        # Поиск по пользователям
        results = await search_users(query)
        if not results:
            return HTMLResponse(f'''
                <div class="error">❌ Пользователь "{query}" не найден</div>
                <div style="text-align:center; margin:20px;">
                    <a class="btn" href="/admin">🔍 Новый поиск</a>
                    <a class="btn" href="/admin/update_index">🔄 Обновить индекс</a>
                </div>
            ''')
        
        for user_id, user_data in results.items():
            chats_count = len(user_data.get('chats', []))
            results_html += f'''
            <div class="result-item">
                <div class="result-chat">👤 Найден пользователь:</div>
                <div class="result-message">
                    <strong>ID:</strong> {user_id}<br>
                    <strong>Username:</strong> @{user_data.get('username', '')}<br>
                    <strong>Имя:</strong> {user_data.get('first_name', '')} {user_data.get('last_name', '')}<br>
                    <strong>Телефон:</strong> {user_data.get('phone', 'Не указан')}
                </div>
                <div class="result-meta">
                    📊 Упоминаний в чатах: {chats_count}
                </div>
            </div>
            '''
    
    return f"""
    <html>
    <head>
        <link rel="stylesheet" href="/static/style.css">
        <title>Результаты поиска: {query}</title>
    </head>
    <body>
        <div style="text-align:center; padding:20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
            <h1>🔍 Результаты поиска: "{query}"</h1>
            <p>Тип поиска: {"🔤 Ключевые слова" if type == "keyword" else "👤 Пользователи"}</p>
        </div>
        
        <div style="text-align:center; margin:20px;">
            <a class="btn" href="/admin">🔍 Новый поиск</a>
            <a class="btn" href="/chats">💬 К чатам</a>
            <a class="btn" href="/">🏠 На главную</a>
        </div>
        
        <div style="max-width: 1000px; margin: 0 auto; padding: 20px;">
            {results_html if results_html else '<div class="error">❌ Ничего не найдено</div>'}
        </div>
    </body>
    </html>
    """

def highlight_text(text, query):
    """Подсветка найденных слов в тексте"""
    if not text or not query:
        return text
    
    words = query.lower().split()
    highlighted = str(text)
    
    for word in words:
        if len(word) > 2:  # Подсвечиваем только слова длиннее 2 символов
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            highlighted = pattern.sub(f'<span class="highlight">{word}</span>', highlighted)
    
    return highlighted

async def search_keywords(query):
    """Поиск по ключевым словам"""
    results = {}
    query_words = query.lower().split()
    
    for word in query_words:
        if word in search_index['keywords']:
            for chat_id, message_ids in search_index['keywords'][word].items():
                if chat_id not in results:
                    results[chat_id] = []
                
                for msg_id in message_ids:
                    if msg_id in search_index['messages']:
                        results[chat_id].append(search_index['messages'][msg_id])
    
    # Сортируем результаты по дате (новые сначала)
    for chat_id in results:
        results[chat_id].sort(key=lambda x: x.get('date', ''), reverse=True)
    
    return results

async def search_users(query):
    """Поиск по пользователям"""
    results = {}
    query_lower = query.lower()
    
    for user_id, user_data in search_index['users'].items():
        # Ищем по ID
        if query_lower in str(user_id).lower():
            results[user_id] = user_data
            continue
            
        # Ищем по username
        username = user_data.get('username', '').lower()
        if query_lower in username:
            results[user_id] = user_data
            continue
            
        # Ищем по имени
        first_name = user_data.get('first_name', '').lower()
        last_name = user_data.get('last_name', '').lower()
        full_name = f"{first_name} {last_name}".strip().lower()
        
        if query_lower in first_name or query_lower in last_name or query_lower in full_name:
            results[user_id] = user_data
    
    return results

@app.get("/admin/update_index")
async def update_search_index():
    """Обновление индекса поиска по всем чатам"""
    if not clients:
        return {"error": "Нет активной сессии"}
    
    client = list(clients.values())[0]
    
    try:
        # Очищаем старый индекс
        search_index.clear()
        search_index.update({
            'keywords': {},
            'users': {},
            'messages': {},
            'chats': {}
        })
        
        # Получаем все диалоги
        dialogs = await client.get_dialogs(limit=100)
        total_messages = 0
        
        for dialog in dialogs:
            entity = dialog.entity
            chat_id = entity.id
            chat_title = getattr(entity, 'title', 'Личная переписка')
            
            # Сохраняем информацию о чате
            search_index['chats'][chat_id] = {
                'title': chat_title,
                'type': 'group' if hasattr(entity, 'participants_count') else 'private',
                'link': get_chat_link(entity)
            }
            
            print(f"🔍 Индексируем чат: {chat_title}")
            
            # Индексируем сообщения
            async for message in client.iter_messages(entity, limit=1000):
                await index_message(message, chat_id)
                total_messages += 1
                
                # Прогресс каждые 100 сообщений
                if total_messages % 100 == 0:
                    print(f"📝 Проиндексировано {total_messages} сообщений...")
        
        return {
            "status": "success",
            "words": len(search_index['keywords']),
            "users": len(search_index['users']),
            "messages": len(search_index['messages']),
            "chats": len(search_index['chats']),
            "total_messages": total_messages
        }
        
    except Exception as e:
        return {"error": safe_error_message(e)}

async def index_message(message, chat_id):
    """Индексация одного сообщения для поиска"""
    try:
        message_id = f"{chat_id}_{message.id}"
        
        # Базовые данные сообщения
        message_data = {
            'id': message_id,
            'chat_id': chat_id,
            'date': message.date.strftime('%d.%m.%Y %H:%M') if message.date else '',
            'text': '',
            'sender': '',
            'sender_id': None
        }
        
        # Обрабатываем отправителя
        if message.sender:
            sender_id = message.sender.id
            message_data['sender_id'] = sender_id
            message_data['sender'] = get_sender_name(message.sender)
            
            # Индексируем пользователя
            await index_user(message.sender, chat_id)
        
        # Обрабатываем текст сообщения
        if message.text:
            text = str(message.text)
            message_data['text'] = text
            message_data['message'] = text  # Для отображения в результатах
            
            # Индексируем ключевые слова
            await index_keywords(text, chat_id, message_id)
        
        # Сохраняем сообщение
        search_index['messages'][message_id] = message_data
        
    except Exception as e:
        print(f"❌ Ошибка индексации сообщения: {safe_error_message(e)}")

async def index_user(user, chat_id):
    """Индексация пользователя"""
    try:
        user_id = user.id
        
        if user_id not in search_index['users']:
            search_index['users'][user_id] = {
                'username': getattr(user, 'username', ''),
                'first_name': getattr(user, 'first_name', ''),
                'last_name': getattr(user, 'last_name', ''),
                'phone': getattr(user, 'phone', ''),
                'chats': set()
            }
        
        # Добавляем чат в список где встречался пользователь
        search_index['users'][user_id]['chats'].add(chat_id)
        
    except Exception as e:
        print(f"❌ Ошибка индексации пользователя: {safe_error_message(e)}")

async def index_keywords(text, chat_id, message_id):
    """Индексация ключевых слов из текста"""
    try:
        # Очищаем текст и разбиваем на слова
        words = extract_keywords(text)
        
        for word in words:
            if word not in search_index['keywords']:
                search_index['keywords'][word] = {}
            
            if chat_id not in search_index['keywords'][word]:
                search_index['keywords'][word][chat_id] = []
            
            if message_id not in search_index['keywords'][word][chat_id]:
                search_index['keywords'][word][chat_id].append(message_id)
                
    except Exception as e:
        print(f"❌ Ошибка индексации ключевых слов: {safe_error_message(e)}")

def extract_keywords(text):
    """Извлечение ключевых слов из текста"""
    # Очищаем текст
    clean_text = re.sub(r'[^\w\s]', ' ', str(text).lower())
    
    # Разбиваем на слова
    words = clean_text.split()
    
    # Фильтруем стоп-слова и короткие слова
    stop_words = {'и', 'в', 'на', 'с', 'по', 'для', 'не', 'что', 'это', 'как', 'так', 'вот', 'но', 'а', 'же', 'ли', 'бы', 'то', 'о', 'у', 'из', 'от', 'к', 'до', 'за', 'же', 'вы', 'ты', 'я', 'мы', 'он', 'она', 'они', 'оно'}
    
    keywords = []
    for word in words:
        if (len(word) > 2 and 
            word not in stop_words and 
            not word.isdigit() and
            not word.startswith('http')):
            keywords.append(word)
    
    return keywords

def get_sender_name(sender):
    """Получение имени отправителя"""
    try:
        if hasattr(sender, 'username') and sender.username:
            return f"@{sender.username}"
        elif hasattr(sender, 'first_name') and sender.first_name:
            name = sender.first_name
            if hasattr(sender, 'last_name') and sender.last_name:
                name += f" {sender.last_name}"
            return name
        else:
            return f"User {sender.id}"
    except:
        return "Unknown"

@app.get("/admin/clear_index")
async def clear_search_index():
    """Очистка индекса поиска"""
    search_index.clear()
    search_index.update({
        'keywords': {},
        'users': {}, 
        'messages': {},
        'chats': {}
    })
    return {"status": "index_cleared"}

# ОСТАЛЬНЫЕ СУЩЕСТВУЮЩИЕ ФУНКЦИИ

@app.get("/download_period/{chat_id}")
async def download_period(chat_id: int, days: str = "all", format: str = "html"):
    """Скачать историю чата за период с медиафайлами"""
    return await handle_period_download(chat_id, days, format, with_media=True)

@app.get("/download_period_fast/{chat_id}")
async def download_period_fast(chat_id: int, days: str = "all", format: str = "html"):
    """Быстрое скачивание истории чата за период (без медиа)"""
    return await handle_period_download(chat_id, days, format, with_media=False)

@app.get("/download_custom_period/{chat_id}")
async def download_custom_period(chat_id: int, start_date: str, end_date: str, format: str):
    """Скачать историю чата за пользовательский период"""
    return HTMLResponse(f'<div class="success">📅 Кастомный период: {start_date} - {end_date}, формат: {format}</div>')

async def handle_period_download(chat_id: int, days: str, format: str, with_media: bool):
    """Обработчик скачивания за период"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    return HTMLResponse(f'''
        <div class="success">
            📥 Запрос на скачивание:<br>
            Чат ID: {chat_id}<br>
            Период: {days}<br>
            Формат: {format}<br>
            Медиа: {'Да' if with_media else 'Нет'}<br>
            <em>Функция в разработке...</em>
        </div>
        <a class="btn" href="/chat/{chat_id}">← Назад к чату</a>
    ''')

@app.get("/export_all")
async def export_all():
    """Экспорт всех чатов в HTML и CSV"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    
    # Очищаем предыдущие экспорты
    for folder in ["chats", "csv", "participants"]:
        folder_path = os.path.join(EXPORTS_DIR, folder)
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)

    dialogs = await client.get_dialogs()
    
    results = []
    total_messages = 0
    
    for dialog in dialogs:
        try:
            chat_data = await export_chat_history(client, dialog)
            results.append(chat_data)
            total_messages += chat_data['messages_count']
        except Exception as e:
            print(f"Ошибка экспорта чата {dialog.name}: {safe_error_message(e)}")
    
    # Создаем ZIP архив
    zip_filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = os.path.join(EXPORTS_DIR, "archives", zip_filename)
    create_zip_archive(zip_path)
    
    return HTMLResponse(f'''
        <div class="success">
            ✅ Экспорт завершен!<br>
            Обработано чатов: {len(results)}<br>
            Сообщений: {total_messages}<br>
            <a class="btn" href="/download_export">📥 Скачать ZIP архив</a>
        </div>
        <a class="btn" href="/chats">← Назад к чатам</a>
    ''')

async def export_chat_history(client, dialog):
    """Экспорт истории конкретного чата"""
    entity = dialog.entity
    chat_id = entity.id
    chat_title = getattr(entity, 'title', 'Личная переписка')
    
    # Собираем метаданные
    chat_info = {
        'id': chat_id,
        'title': chat_title,
        'type': 'group' if hasattr(entity, 'participants_count') else 'private',
        'export_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Собираем историю сообщений и участников одновременно
    messages_html = ""
    messages_csv = []
    participants = {}  # Используем словарь чтобы избежать дубликатов
    
    async for message in client.iter_messages(entity, limit=200):
        msg_data = process_message(message)
        messages_html += msg_data['html']
        messages_csv.append(msg_data['csv'])
        
        # Собираем участников из отправителей сообщений
        if message.sender:
            sender_id = message.sender.id
            if sender_id not in participants:
                participants[sender_id] = {
                    'id': sender_id,
                    'username': getattr(message.sender, 'username', ''),
                    'first_name': getattr(message.sender, 'first_name', ''),
                    'last_name': getattr(message.sender, 'last_name', '')
                }
    
    # Преобразуем словарь обратно в список
    participants_list = list(participants.values())
    
    # Если это группа/канал и участников мало, пробуем получить полный список
    if hasattr(entity, 'participants_count') and len(participants_list) < 10:
        try:
            async for user in client.iter_participants(entity, limit=50):
                user_id = user.id
                if user_id not in participants:
                    participants[user_id] = {
                        'id': user_id,
                        'username': user.username or '',
                        'first_name': user.first_name or '',
                        'last_name': user.last_name or ''
                    }
            participants_list = list(participants.values())
        except Exception as e:
            print(f"Не удалось получить участников чата {chat_title}: {safe_error_message(e)}")
            # Продолжаем с участниками из сообщений
    
    # Сохраняем HTML файл с правильной кодировкой
    html_content = create_chat_html(chat_info, participants_list, messages_html, len(messages_csv))
    html_path = os.path.join(EXPORTS_DIR, "chats", f"chat_{chat_id}.html")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        print(f"❌ Ошибка записи HTML файла: {safe_error_message(e)}")
        # Пробуем альтернативный способ
        try:
            with open(html_path, "wb") as f:
                f.write(html_content.encode('utf-8'))
        except Exception as e2:
            print(f"❌ Критическая ошибка записи HTML: {safe_error_message(e2)}")
    
    # Сохраняем CSV с правильной кодировкой
    csv_path = os.path.join(EXPORTS_DIR, "csv", f"chat_{chat_id}.csv")
    try:
        with open(csv_path, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'sender_id', 'sender_username', 'message_type', 'content'])
            writer.writerows(messages_csv)
    except Exception as e:
        print(f"❌ Ошибка записи CSV файла: {safe_error_message(e)}")
    
    # Сохраняем участников с правильной кодировкой
    json_path = os.path.join(EXPORTS_DIR, "participants", f"chat_{chat_id}.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                'chat_info': chat_info,
                'participants': participants_list,
                'participants_source': 'from_messages' if len(participants_list) > 0 else 'unknown'
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка записи JSON файла: {safe_error_message(e)}")
    
    return {
        'id': chat_id,
        'title': chat_title,
        'messages_count': len(messages_csv),
        'participants_count': len(participants_list)
    }

def process_message(message):
    """Обработка одного сообщения для HTML и CSV"""
    # Определяем тип сообщения и отправителя
    if message.out:
        sender_type = "Исходящее"
        sender_info = f"Вы (ID {message.sender_id})"
    else:
        sender = message.sender
        if sender:
            username = f"@{sender.username}" if sender.username else ""
            sender_info = f"{username} (ID {sender.id})"
        else:
            sender_info = f"Unknown (ID {message.sender_id})"
        sender_type = "Входящее"
    
    # Обрабатываем контент сообщения
    if message.text:
        content = message.text
    elif message.media:
        content = f"[{get_media_type(message.media)}]"
    else:
        content = "[Пустое сообщение]"
    
    # HTML версия
    html = f"""
    <div class="message {'outgoing' if message.out else 'incoming'}">
        <div class="message-header">
            <strong>{sender_type}: {sender_info}</strong>
            <span class="message-time">{message.date.strftime('%d.%m.%Y %H:%M:%S')}</span>
        </div>
        <div class="message-content">{content}</div>
    </div>
    """
    
    # CSV версия
    csv_row = [
        message.date.strftime('%d.%m.%Y %H:%M:%S'),
        message.sender_id,
        getattr(message.sender, 'username', '') if message.sender else '',
        'outgoing' if message.out else 'incoming',
        str(content)[:500]  # ограничиваем длину для CSV
    ]
    
    return {'html': html, 'csv': csv_row}

def create_chat_html(chat_info, participants, messages_html, total_messages):
    """Создает HTML файл с историей чата"""
    participants_source = "из истории сообщений" if participants and any(p['username'] for p in participants) else "не удалось получить"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>История чата: {chat_info['title']}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .message {{ margin: 10px 0; padding: 10px; border-radius: 5px; }}
            .outgoing {{ background: #e3f2fd; margin-left: 50px; }}
            .incoming {{ background: #f5f5f5; margin-right: 50px; }}
            .message-header {{ display: flex; justify-content: space-between; }}
            .message-time {{ color: #666; font-size: 0.9em; }}
            .participants {{ background: #eee; padding: 15px; margin: 20px 0; }}
            .chat-info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; }}
            .source-note {{ color: #666; font-size: 0.9em; font-style: italic; }}
            .stats {{ background: #e8f5e8; padding: 10px; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>💬 Чат: {chat_info['title']}</h1>
        
        <div class="stats">
            <strong>📊 Статистика:</strong> {total_messages} сообщений, {len(participants)} участников
        </div>
        
        <div class="chat-info">
            <p><strong>ID чата:</strong> {chat_info['id']}</p>
            <p><strong>Тип:</strong> {chat_info['type']}</p>
            <p><strong>Дата экспорта:</strong> {chat_info['export_date']}</p>
        </div>
        
        <div class="participants">
            <h3>👥 Участники ({len(participants)}):</h3>
            <p class="source-note">Источник: {participants_source}</p>
            {'<br>'.join([f"@{p['username']} (ID: {p['id']}) - {p['first_name']} {p['last_name']}" for p in participants if p['username']])}
            {'' if any(p['username'] for p in participants) else '<p>Участники собраны из истории сообщений</p>'}
        </div>
        
        <h3>📝 История сообщений ({total_messages}):</h3>
        <div class="messages">
            {messages_html if messages_html else '<p>Сообщений не найдено</p>'}
        </div>
    </body>
    </html>
    """

def create_zip_archive(zip_path):
    """Создает ZIP архив со всеми экспортированными файлами"""
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for folder in ["chats", "csv", "participants"]:
            folder_path = os.path.join(EXPORTS_DIR, folder)
            for filename in os.listdir(folder_path):
                file_path = os.path.join(folder_path, filename)
                if os.path.isfile(file_path):
                    zipf.write(file_path, f"{folder}/{filename}")

@app.get("/download_export")
async def download_export():
    """Скачать последний созданный архив"""
    archives_dir = os.path.join(EXPORTS_DIR, "archives")
    if not os.path.exists(archives_dir) or not os.listdir(archives_dir):
        return HTMLResponse('<div class="error">❌ Архив не найден. Сначала выполните экспорт.</div>')
    
    # Находим последний архив
    archives = [f for f in os.listdir(archives_dir) if f.endswith('.zip')]
    if not archives:
        return HTMLResponse('<div class="error">❌ Архив не найден.</div>')
    
    latest_archive = sorted(archives)[-1]  # последний по времени
    archive_path = os.path.join(archives_dir, latest_archive)
    
    # Кодируем имя файла для безопасной загрузки
    encoded_filename = urllib.parse.quote(latest_archive)
    
    return FileResponse(
        archive_path,
        filename=f"telegram_export_{datetime.now().strftime('%Y%m%d')}.zip",
        media_type='application/zip',
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
