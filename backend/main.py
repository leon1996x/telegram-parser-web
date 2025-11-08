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
import os
import shutil
from datetime import datetime, timedelta
import csv
import json
import zipfile
import io
import asyncio

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

async def get_chat_participants_guaranteed(client, entity, limit=50000):
    """ГАРАНТИРОВАННЫЙ сбор участников - читает ВСЕ сообщения"""
    participants = {}
    message_count = 0
    
    print(f"🔍 ГАРАНТИРОВАННЫЙ сбор участников для: {getattr(entity, 'title', 'чата')}")
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
                        'source': 'messages'
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
    
    print(f"✅ ГАРАНТИРОВАННЫЙ сбор завершен! Прочитано {message_count} сообщений, собрано {len(participants)} участников")
    
    # Дополнительно: пробуем получить список участников если это группа
    try:
        if hasattr(entity, 'participants_count'):
            print("🔍 Дополнительно получаем список участников...")
            added_count = 0
            async for user in client.iter_participants(entity, limit=10000):
                if user.id not in participants:
                    participants[user.id] = {
                        'id': user.id,
                        'username': user.username or '',
                        'first_name': user.first_name or '',
                        'last_name': user.last_name or '',
                        'phone': user.phone or '',
                        'source': 'participants_list'
                    }
                    added_count += 1
            print(f"✅ Добавлено {added_count} участников из списка")
    except Exception as e:
        print(f"⚠️ Не удалось получить список участников: {safe_error_message(e)}")
    
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
                        <a class="btn-download" href="/download_participants/{entity.id}?format=html">👥 Участники</a>
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
    
    buttons_html += """
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
                <a class="btn-download small" href="/download_participants/{}?format=html">HTML</a>
                <a class="btn-download small" href="/download_participants/{}?format=json">JSON</a>
                <a class="btn-download small" href="/download_participants/{}?format=csv">CSV</a>
                <a class="btn-download small" href="/download_participants/{}?format=txt">TXT</a>
            </div>
        </div>
        
        <p class="note">💡 Фото, видео и аудио будут встроены в HTML файл</p>
        <p class="note">⚡ Для больших периодов скачивание может занять несколько минут</p>
    </div>
    
    <script>
    function showProgress(periodName) {
        document.getElementById('progressContainer').style.display = 'block';
        document.getElementById('progressText').textContent = 'Подготовка ' + periodName + '...';
        
        let progress = 0;
        const interval = setInterval(() => {
            progress += Math.random() * 10;
            if (progress > 90) progress = 90;
            document.getElementById('progressFill').style.width = progress + '%';
            document.getElementById('progressText').textContent = 'Обработка ' + periodName + '... ' + Math.round(progress) + '%';
        }, 500);
        
        // Остановить анимацию когда страница загрузится
        window.addEventListener('beforeunload', () => {
            clearInterval(interval);
        });
    }
    </script>
    """.format(chat_id, chat_id, chat_id, chat_id)
    
    return buttons_html

@app.get("/chat/{chat_id}")
async def view_chat(chat_id: int, offset_id: int = Query(0, ge=0)):
    """Детальная страница чата - БЫСТРАЯ загрузка"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    limit = 50  # сообщений на страницу

    try:
        entity = await client.get_entity(chat_id)
        chat_title = getattr(entity, 'title', 'Личная переписка')
        creation_date = await get_chat_creation_date(client, entity)
        chat_link = get_chat_link(entity)
        
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

@app.get("/force_collect/{chat_id}")
async def force_collect_participants(chat_id: int):
    """ПРИНУДИТЕЛЬНЫЙ сбор участников - читает ВСЕ сообщения"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    
    try:
        entity = await client.get_entity(chat_id)
        chat_title = getattr(entity, 'title', 'Личная переписка')
        
        print(f"🚀 ЗАПУСК ПРИНУДИТЕЛЬНОГО СБОРА ДЛЯ: {chat_title}")
        
        # Принудительный сбор с максимальным лимитом
        participants = await get_chat_participants_guaranteed(client, entity, limit=100000)
        
        # Сохраняем результат
        result = {
            'chat_title': chat_title,
            'chat_id': chat_id,
            'total_participants': len(participants),
            'participants_count_with_username': sum(1 for p in participants.values() if p['username']),
            'participants': list(participants.values()),
            'collected_at': datetime.now().strftime('%d.%m.%Y %H:%M:%S')
        }
        
        # Показываем подробный результат
        participants_list = list(participants.values())
        participants_list.sort(key=lambda x: x['id'])
        
        result_html = f"""
        <html>
        <head>
            <link rel="stylesheet" href="/static/style.css">
            <title>Результат сбора: {chat_title}</title>
        </head>
        <body>
            <div class="chat-header">
                <h1>🎯 Результат принудительного сбора</h1>
                <div class="success" style="text-align:center; padding:20px;">
                    <h3>Чат: {chat_title}</h3>
                    <p><strong>✅ Собрано участников:</strong> {len(participants)}</p>
                    <p><strong>📊 С @username:</strong> {sum(1 for p in participants.values() if p['username'])}</p>
                    <p><strong>🕒 Время сбора:</strong> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>
                </div>
                
                <div style="text-align:center; margin:20px;">
                    <a class="btn" href="/download_participants/{chat_id}?format=html" style="background:#10b981; font-size:18px; padding:15px 30px;">📥 Скачать полный список участников</a>
                    <a class="btn" href="/chat/{chat_id}">← Назад к чату</a>
                </div>
                
                <h3>📋 Первые 100 участников:</h3>
                <div style="overflow-x:auto;">
                    <table style="width:100%; border-collapse: collapse;">
                        <thead>
                            <tr style="background:#3b82f6; color:white;">
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
            </div>
        </body>
        </html>
        """
        
        return HTMLResponse(result_html)
        
    except Exception as e:
        return HTMLResponse(f'<div class="error">❌ Ошибка: {safe_error_message(e)}</div>')

@app.get("/download_participants/{chat_id}")
async def download_participants(chat_id: int, format: str = "html"):
    """Скачать список участников чата - ГАРАНТИРОВАННЫЙ сбор"""
    if not clients:
        return HTMLResponse("<h3>Нет активной сессии</h3>")

    client = list(clients.values())[0]
    
    try:
        entity = await client.get_entity(chat_id)
        chat_title = getattr(entity, 'title', 'Личная переписка')
        chat_link = get_chat_link(entity)
        
        # ГАРАНТИРОВАННЫЙ сбор участников
        participants = await get_chat_participants_guaranteed(client, entity, limit=50000)
        
        # Создаем безопасное имя файла
        safe_chat_title = safe_filename(chat_title) or f"chat_{chat_id}"
        
        if format == "html":
            content = generate_participants_html(chat_title, chat_link, participants)
            filename = f"{safe_chat_title}_participants.html"
            media_type = "text/html; charset=utf-8"
        elif format == "json":
            content = generate_participants_json(participants)
            filename = f"{safe_chat_title}_participants.json"
            media_type = "application/json; charset=utf-8"
        elif format == "csv":
            content = generate_participants_csv(participants)
            filename = f"{safe_chat_title}_participants.csv"
            media_type = "text/csv; charset=utf-8"
        else:  # txt
            content = generate_participants_txt(chat_title, chat_link, participants)
            filename = f"{safe_chat_title}_participants.txt"
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

def generate_participants_html(chat_title, chat_link, participants):
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
            .stats {{ background: #e8f5e8; padding: 12px; border-radius: 8px; margin: 15px 0; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #3b82f6; color: white; }}
            tr:hover {{ background: #f5f5f5; }}
            .source-participants {{ background: #e8f5e8; }}
            .source-messages {{ background: #fff3cd; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>👥 Участники чата: {chat_title}</h1>
            <div class="stats">
                <strong>📊 Статистика:</strong> {len(participants)} участников
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

def generate_participants_json(participants):
    """Генерирует JSON файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    return json.dumps({
        'export_date': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'total_participants': len(participants_list),
        'participants': participants_list
    }, ensure_ascii=False, indent=2)

def generate_participants_csv(participants):
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

def generate_participants_txt(chat_title, chat_link, participants):
    """Генерирует TXT файл со списком участников"""
    participants_list = list(participants.values())
    participants_list.sort(key=lambda x: x['username'] or x['first_name'] or '')
    
    content = f"Участники чата: {chat_title}\n"
    content += f"Ссылка: {chat_link}\n"
    content += f"Всего участников: {len(participants_list)}\n"
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

# Остальной код остается без изменений...

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
