from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <title>Telegram Parser Web</title>
        </head>
        <body style='font-family: sans-serif; text-align: center; margin-top: 100px;'>
            <h1>✅ Telegram Parser Web — работает!</h1>
            <p>Бэкенд на FastAPI запущен успешно 🚀</p>
        </body>
    </html>
    """
