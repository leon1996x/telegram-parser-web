from fastapi import FastAPI, Form, UploadFile, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
import os
import shutil
from datetime import datetime
import csv
import json
import zipfile

app = FastAPI()

# ПРАВИЛЬНЫЕ ПУТИ ДЛЯ RENDER
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
AVATAR_DIR = os.path.join(BASE_DIR, "static", "avatars")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
DATABASE_DIR = os.path.join(BASE_DIR, "database")

# Создаем все необходимые папки
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)
os.makedirs(DATABASE_DIR, exist_ok=True)

# Создаем подпапки exports
for folder in ["chats", "csv", "participants", "archives"]:
    os.makedirs(os.path.join(EXPORTS_DIR, folder), exist_ok=True)

clients = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(TEMPLATES_DIR, "index.html"), "r", encoding="utf-8") as f:
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

        # Безопасное извлечение последнего сообщения
        if dialog.message and getattr(dialog.message, "message", None):
            last_message = dialog.message.message
        else:
            media_type = getattr(dialog.message, "media", None)
            if media_type:
                last_message = f"[Медиа]"
            else:
                last_message = "(нет сообщений)"

        # Получаем время сообщения с датой
        if dialog.message and hasattr(dialog.message, 'date'):
            message_date = dialog.message.date
            # Если сообщение сегодня - показываем время, иначе дату
            if message_date.date() == datetime.now().date():
                message_time = message_date.strftime("%H:%M")
            else:
                message_time = message_date.strftime("%d.%m.%y")
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
                <div class="chat-title">{title}</div>
                <div class="chat-last">{safe_message}</div>
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

    html += '<div class="back"><a href="/">↩ На главную</a></div>'
    html += "</body></html>"

    return HTMLResponse(html)


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
            print(f"Ошибка экспорта чата {dialog.name}: {e}")
    
    # Создаем ZIP архив
    zip_path = os.path.join(EXPORTS_DIR, "archives", f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
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
    
    async for message in client.iter_messages(entity, limit=200):  # увеличим лимит
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
            print(f"Не удалось получить участников чата {chat_title}: {e}")
            # Продолжаем с участниками из сообщений
    
    # Сохраняем HTML файл
    html_content = create_chat_html(chat_info, participants_list, messages_html)
    with open(os.path.join(EXPORTS_DIR, "chats", f"chat_{chat_id}.html"), "w", encoding="utf-8") as f:
        f.write(html_content)
    
    # Сохраняем CSV
    with open(os.path.join(EXPORTS_DIR, "csv", f"chat_{chat_id}.csv"), "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'sender_id', 'sender_username', 'message_type', 'content'])
        writer.writerows(messages_csv)
    
    # Сохраняем участников
    with open(os.path.join(EXPORTS_DIR, "participants", f"chat_{chat_id}.json"), "w", encoding="utf-8") as f:
        json.dump({
            'chat_info': chat_info,
            'participants': participants_list,
            'participants_source': 'from_messages' if len(participants_list) > 0 else 'unknown'
        }, f, ensure_ascii=False, indent=2)
    
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
        content = f"[Медиа: {type(message.media).__name__}]"
    else:
        content = "[Пустое сообщение]"
    
    # HTML версия
    html = f"""
    <div class="message {'outgoing' if message.out else 'incoming'}">
        <div class="message-header">
            <strong>{sender_type}: {sender_info}</strong>
            <span class="message-time">{message.date.strftime('%Y-%m-%d %H:%M:%S')}</span>
        </div>
        <div class="message-content">{content}</div>
    </div>
    """
    
    # CSV версия
    csv_row = [
        message.date.strftime('%Y-%m-%d %H:%M:%S'),
        message.sender_id,
        getattr(message.sender, 'username', '') if message.sender else '',
        'outgoing' if message.out else 'incoming',
        str(content)[:500]  # ограничиваем длину для CSV
    ]
    
    return {'html': html, 'csv': csv_row}


def create_chat_html(chat_info, participants, messages_html):
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
        </style>
    </head>
    <body>
        <h1>💬 Чат: {chat_info['title']}</h1>
        
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
        
        <h3>📝 История сообщений:</h3>
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
    
    return FileResponse(
        archive_path,
        filename=f"telegram_export_{datetime.now().strftime('%Y%m%d')}.zip",
        media_type='application/zip'
    )
