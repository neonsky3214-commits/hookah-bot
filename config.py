import os

BOT_TOKEN = os.environ.get("8777411837:AAElTBxbr6IBmVaW4JU6qpDZhypieZ_wHGI", "")
ADMIN_CHAT_ID = int(os.environ.get("-1003658423562", "0"))
ADMIN_IDS = [int(x) for x in os.environ.get("520032441", "0").split(",")]
