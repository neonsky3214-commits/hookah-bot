import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

# URL где живёт твой Mini App — заполнится после деплоя на Railway
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
