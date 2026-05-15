"""
LIWAN x Loona Loyalty Integration
Инструкция: POS система типа API, client_id = идентификационный номер POS системы
Auth: POST /oauth/token, Basic base64(client_id:client_secret)
"""
import aiohttp
import logging
import os
import base64
import time
import json

logger = logging.getLogger(__name__)

LOONA_BASE          = "https://api.loona.ai/version1"
LOONA_TOKEN_URL     = "https://api.loona.ai/oauth/token"
LOONA_CLIENT_ID     = os.environ.get("LOONA_CLIENT_ID", "1674")
LOONA_CLIENT_SECRET = os.environ.get("LOONA_CLIENT_SECRET", "")
LOONA_TEMPLATE_ID   = os.environ.get("LOONA_TEMPLATE_ID", "1674")

VAR_BALANCE    = "balance"
VAR_PERCENTAGE = "percentage"
VAR_SPENT      = "totalSpent"
VAR_VISITS     = "transactionsCount"

_token_cache = {"token": None, "expires_at": 0}


async def get_token() -> str | None:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 10:
        return _token_cache["token"]

    if not LOONA_CLIENT_SECRET:
        logger.error("LOONA_CLIENT_SECRET не задан")
        return None

    creds = f"{LOONA_CLIENT_ID}:{LOONA_CLIENT_SECRET}"
    encoded = base64.b64encode(creds.encode("utf-8")).decode("utf-8")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                LOONA_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data="grant_type=client_credentials",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                logger.info(f"Loona /oauth/token → {r.status}: {body[:200]}")
                if r.status == 200:
                    data = json.loads(body)
                    token = data["access_token"]
                    _token_cache["token"] = token
                    _token_cache["expires_at"] = now + data.get("expires_in", 299)
                    return token
                return None
    except Exception as e:
        logger.error(f"Loona get_token error: {e}")
        return None


def _hdrs(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def create_card(name: str, phone: str, email: str = "") -> dict | None:
    token = await get_token()
    if not token:
        return None

    # Split name into first/last
    parts = name.strip().split(" ", 1)
    first_name = parts[0] if parts else name
    last_name = parts[1] if len(parts) > 1 else ""

    # Format phone: ensure +7 format
    if phone.startswith("8") and len(phone) == 11:
        phone = "+7" + phone[1:]
    elif not phone.startswith("+"):
        phone = "+" + phone

    payload = {
        "templateId": int(LOONA_TEMPLATE_ID),
        "placeholderValues": [
            {"name": "firstName", "value": first_name},
            {"name": "lastName",  "value": last_name},
            {"name": "phone",     "value": phone},
            {"name": "gender",    "value": "MALE"},
            {"name": "birthday",  "value": "2000-01-01"},
        ],
    }
    if email:
        payload["placeholderValues"].append({"name": "email", "value": email})

    logger.info(f"Loona create_card payload: {json.dumps(payload)[:500]}")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{LOONA_BASE}/passes",
                json=payload,
                headers=_hdrs(token),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                logger.info(f"Loona create_card → {r.status}: {body[:500]}")
                if r.status in (200, 201):
                    return json.loads(body)
                # Card already exists - find it by phone
                if r.status == 400 and "already exists" in body:
                    logger.info(f"Card already exists for {phone}, searching...")
                    return await find_card_by_phone(phone, token)
                return None
    except Exception as e:
        logger.error(f"Loona create_card error: {e}")
        return None



async def find_card_by_phone(phone: str, token: str = None) -> dict | None:
    """Find existing card by phone number using search API"""
    if not token:
        token = await get_token()
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            # Search by phone
            async with s.post(
                f"{LOONA_BASE}/passes/search",
                json={
                    "templateId": int(LOONA_TEMPLATE_ID),
                    "phone": phone
                },
                headers=_hdrs(token),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                logger.info(f"Loona search by phone {phone} → {r.status}: {body[:300]}")
                if r.status == 200:
                    data = json.loads(body)
                    items = data.get("content") or data.get("items") or []
                    if items:
                        logger.info(f"Found card for {phone}: id={items[0].get('id')}")
                        return items[0]
        return None
    except Exception as e:
        logger.error(f"Loona find_card error: {e}")
        return None

async def get_card(pass_id: str) -> dict | None:
    token = await get_token()
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{LOONA_BASE}/passes/{pass_id}",
                headers=_hdrs(token),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    return json.loads(await r.text())
                logger.error(f"Loona get_card → {r.status}")
                return None
    except Exception as e:
        logger.error(f"Loona get_card error: {e}")
        return None


async def update_card(pass_id: str, balance: int, percentage: int,
                      total_spent: int, visits: int) -> bool:
    token = await get_token()
    if not token:
        return False
    # Get current card first to preserve all fields
    current = await get_card(pass_id)
    current_vals = {}
    if current:
        for v in current.get("placeholderValues", []):
            current_vals[v["name"]] = v["value"]

    # Update only our fields, keep rest as is
    current_vals[VAR_BALANCE]    = str(balance)
    current_vals[VAR_PERCENTAGE] = str(percentage)
    current_vals[VAR_SPENT]      = str(total_spent)
    current_vals[VAR_VISITS]     = str(visits)

    payload = {
        "placeholderValues": [
            {"name": k, "value": v} for k, v in current_vals.items()
        ]
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.put(
                f"{LOONA_BASE}/passes/{pass_id}",
                json=payload,
                headers=_hdrs(token),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                logger.info(f"Loona update_card → {r.status}: {body[:200]}")
                return r.status == 200
    except Exception as e:
        logger.error(f"Loona update_card error: {e}")
        return False


# ─── Бизнес-логика ────────────────────────────────────────────────────────────

def get_percentage_for_visits(visits: int) -> int:
    if visits < 20: return 0
    if visits < 50: return 5
    if visits < 70: return 7
    return 10

def get_max_payment_pct(visits: int) -> int:
    if visits < 20: return 0
    if visits < 50: return 10
    if visits < 70: return 15
    return 20

def get_level_name(visits: int) -> str:
    if visits < 20: return "Старт"
    if visits < 50: return "Бронза"
    if visits < 70: return "Серебро"
    return "Золото"
