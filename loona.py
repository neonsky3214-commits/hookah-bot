"""
LIWAN x Loona Loyalty Integration
API: https://api.loona.ai/version1
Auth: OAuth2 client_credentials
"""
import aiohttp
import logging
import os
import base64
import time

logger = logging.getLogger(__name__)

LOONA_BASE = "https://api.loona.ai/version1"
LOONA_CLIENT_ID     = os.environ.get("LOONA_CLIENT_ID", "")
LOONA_CLIENT_SECRET = os.environ.get("LOONA_CLIENT_SECRET", "90c7316b-946d-4439-959a-23baa06cd770")
LOONA_TEMPLATE_ID   = os.environ.get("LOONA_TEMPLATE_ID", "1674")

# Переменные карты (должны совпадать с названиями в макете Loona)
VAR_BALANCE    = os.environ.get("LOONA_VAR_BALANCE",    "ownBalance")
VAR_PERCENTAGE = os.environ.get("LOONA_VAR_PERCENTAGE", "ownPercentage")
VAR_SPENT      = os.environ.get("LOONA_VAR_SPENT",      "ownTotalSpent")
VAR_VISITS     = os.environ.get("LOONA_VAR_VISITS",     "ownVisits")

# Token cache
_token_cache = {"token": None, "expires_at": 0}


async def get_access_token() -> str | None:
    """Получить OAuth access token. Кэшируется до истечения срока."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 30:
        return _token_cache["token"]

    if not LOONA_CLIENT_ID or not LOONA_CLIENT_SECRET:
        logger.warning(f"Loona: missing credentials (client_id={LOONA_CLIENT_ID})")
        return None

    # Basic auth: base64(clientId:clientSecret)
    credentials = f"{LOONA_CLIENT_ID}:{LOONA_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    logger.info(f"Loona: authenticating client_id={LOONA_CLIENT_ID}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.loona.ai/oauth/token",
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                data="grant_type=client_credentials"
            ) as resp:
                text = await resp.text()
                if resp.status == 200:
                    import json as _json
                    data = _json.loads(text)
                    token = data.get("access_token")
                    expires_in = data.get("expires_in", 299)
                    _token_cache["token"] = token
                    _token_cache["expires_at"] = now + expires_in
                    logger.info(f"Loona: access token obtained!")
                    return token
                else:
                    logger.error(f"Loona auth error {resp.status}: {text}")
                    return None
    except Exception as e:
        logger.error(f"Loona auth exception: {e}")
        return None


async def _auth_headers() -> dict:
    token = await get_access_token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


async def create_card(name: str, phone: str, email: str = "") -> dict | None:
    """Создать карту лояльности для нового гостя"""
    if not LOONA_TEMPLATE_ID:
        return None
    headers = await _auth_headers()
    if not headers:
        return None

    payload = {
        "templateId": int(LOONA_TEMPLATE_ID),
        "placeholderValues": [
            {"name": VAR_BALANCE,    "value": "0"},
            {"name": VAR_PERCENTAGE, "value": "0"},
            {"name": VAR_SPENT,      "value": "0"},
            {"name": VAR_VISITS,     "value": "0"},
        ],
        "person": {"name": name, "phone": phone}
    }
    if email:
        payload["person"]["email"] = email

    logger.info(f"Loona: creating card for {name}, template={LOONA_TEMPLATE_ID}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{LOONA_BASE}/passes",
                json=payload,
                headers=headers
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    logger.info(f"Loona card created: {data.get('id')}")
                    return data
                else:
                    text = await resp.text()
                    logger.error(f"Loona create_card {resp.status}: {text}")
                    return None
    except Exception as e:
        logger.error(f"Loona create_card exception: {e}")
        return None


async def get_card(pass_id: str) -> dict | None:
    """Получить текущее состояние карты"""
    headers = await _auth_headers()
    if not headers:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{LOONA_BASE}/passes/{pass_id}",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Loona get_card {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Loona get_card exception: {e}")
        return None


async def update_card(pass_id: str, balance: int, percentage: int,
                      total_spent: int, visits: int) -> bool:
    """Обновить состояние карты — передаём ВСЕ переменные"""
    headers = await _auth_headers()
    if not headers:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{LOONA_BASE}/passes/{pass_id}",
                json={
                    "placeholderValues": [
                        {"name": VAR_BALANCE,    "value": str(balance)},
                        {"name": VAR_PERCENTAGE, "value": str(percentage)},
                        {"name": VAR_SPENT,      "value": str(total_spent)},
                        {"name": VAR_VISITS,     "value": str(visits)},
                    ]
                },
                headers=headers
            ) as resp:
                if resp.status == 200:
                    logger.info(f"Loona card {pass_id} updated: visits={visits}, %={percentage}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Loona update_card {resp.status}: {text}")
                    return False
    except Exception as e:
        logger.error(f"Loona update_card exception: {e}")
        return False


async def search_card_by_phone(phone: str) -> dict | None:
    """Найти карту по номеру телефона"""
    headers = await _auth_headers()
    if not headers:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{LOONA_BASE}/passes/search",
                json={"phone": phone, "templateId": int(LOONA_TEMPLATE_ID)},
                headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("content") or data.get("items") or []
                    return items[0] if items else None
                else:
                    return None
    except Exception as e:
        logger.error(f"Loona search exception: {e}")
        return None


# ─── Бизнес-логика ────────────────────────────────────────────────────────────

def get_percentage_for_visits(visits: int) -> int:
    """Кэшбэк по количеству визитов"""
    if visits < 20:  return 0
    if visits < 50:  return 5
    if visits < 70:  return 7
    return 10

def get_max_payment_pct(visits: int) -> int:
    """Максимальный % оплаты баллами"""
    if visits < 20:  return 0
    if visits < 50:  return 10
    if visits < 70:  return 15
    return 20

def get_level_name(visits: int) -> str:
    if visits < 20:  return "Старт"
    if visits < 50:  return "Бронза"
    if visits < 70:  return "Серебро"
    return "Золото"
