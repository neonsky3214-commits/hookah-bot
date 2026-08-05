"""
LIWAN x iiko Integration (iikoCloud / Transport API)
Бронь из бота создаёт резерв в iikoFront (нужен модуль «Банкеты и резервы»,
касса должна быть онлайн). Auth: POST /api/1/access_token {"apiLogin": ...}
→ Bearer token (короткоживущий, кешируем ~10 минут).
"""
import aiohttp
import logging
import os
import time
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

IIKO_BASE                 = os.environ.get("IIKO_BASE", "https://api-ru.iiko.services")
IIKO_API_LOGIN            = os.environ.get("IIKO_API_LOGIN", "")
IIKO_ORGANIZATION_ID      = os.environ.get("IIKO_ORGANIZATION_ID", "")
IIKO_TERMINAL_GROUP_ID    = os.environ.get("IIKO_TERMINAL_GROUP_ID", "")
IIKO_RESERVE_DURATION_MIN = int(os.environ.get("IIKO_RESERVE_DURATION_MIN", "120"))

_token_cache = {"token": None, "expires_at": 0}
# org/terminal group автоопределяются один раз, если не заданы в env
_resolved = {"org_id": None, "terminal_group_id": None}

MONTHS_RU = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
             "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


def is_enabled() -> bool:
    return bool(IIKO_API_LOGIN)


# ─── Чистые хелперы (тестируются без сети) ────────────────────────────────────

def parse_booking_datetime(book_date: str, book_time: str,
                           now: datetime | None = None) -> datetime | None:
    """Mini App шлёт дату как «5 авг», время как «19:00» — год выводим сами:
    если дата уже прошла (больше суток назад), это бронь на следующий год."""
    now = now or datetime.now()
    try:
        day_s, mon_s = book_date.strip().lower().split()
        day = int(day_s)
        month = MONTHS_RU[mon_s[:3]]
        h, m = map(int, book_time.strip().split(":"))
        dt = datetime(now.year, month, day, h, m)
        if dt < now - timedelta(days=1):
            dt = dt.replace(year=now.year + 1)
        return dt
    except (ValueError, KeyError, AttributeError):
        return None


def format_iiko_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


def norm_phone(p: str) -> str:
    p = "".join(ch for ch in (p or "") if ch.isdigit() or ch == "+")
    if p.startswith("8") and len(p) == 11:
        p = "+7" + p[1:]
    elif p.startswith("7") and len(p) == 11:
        p = "+" + p
    elif p and not p.startswith("+"):
        p = "+" + p
    return p


def build_reserve_payload(org_id: str, terminal_group_id: str, table_ids: list,
                          start: datetime, guests: int, name: str, phone: str,
                          comment: str = "",
                          duration_min: int | None = None) -> dict:
    return {
        "organizationId": org_id,
        "terminalGroupId": terminal_group_id,
        "customer": {"name": name or "Гость", "type": "regular"},
        "phone": norm_phone(phone),
        "guestsCount": max(1, int(guests or 1)),
        "comment": comment or "",
        "durationInMinutes": duration_min or IIKO_RESERVE_DURATION_MIN,
        "shouldRemind": False,
        "tableIds": table_ids,
        "estimatedStartTime": format_iiko_dt(start),
    }


# ─── HTTP ─────────────────────────────────────────────────────────────────────

async def get_token() -> str | None:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 10:
        return _token_cache["token"]
    if not IIKO_API_LOGIN:
        logger.error("IIKO_API_LOGIN не задан")
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{IIKO_BASE}/api/1/access_token",
                json={"apiLogin": IIKO_API_LOGIN},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                body = await r.text()
                logger.info(f"iiko /access_token → {r.status}")
                if r.status == 200:
                    token = json.loads(body).get("token")
                    _token_cache["token"] = token
                    _token_cache["expires_at"] = now + 600  # токен живёт ~15 мин
                    return token
                logger.error(f"iiko access_token {r.status}: {body[:300]}")
                return None
    except Exception as e:
        logger.error(f"iiko get_token error: {e}")
        return None


async def _post(path: str, payload: dict) -> dict | None:
    token = await get_token()
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{IIKO_BASE}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                body = await r.text()
                if r.status == 401:  # токен протух раньше времени — сброс кеша
                    _token_cache["token"] = None
                logger.info(f"iiko {path} → {r.status}")
                if r.status == 200:
                    return json.loads(body)
                logger.error(f"iiko {path} {r.status}: {body[:400]}")
                return {"_error": f"HTTP {r.status}: {body[:200]}"}
    except Exception as e:
        logger.error(f"iiko {path} error: {e}")
        return {"_error": str(e)}


# ─── Методы Transport API (раздел Reserves) ───────────────────────────────────

async def get_reserve_organizations() -> list:
    """Организации, где доступны резервы (лицензия «Банкеты и резервы»)."""
    data = await _post("/api/1/reserve/available_organizations",
                       {"returnAdditionalInfo": False, "includeDisabled": False})
    if not data or "_error" in data:
        return []
    return data.get("organizations") or []


async def resolve_org_and_terminal() -> tuple[str | None, str | None]:
    """org/terminal group: из env, иначе первая доступная (кешируем)."""
    if _resolved["org_id"] and _resolved["terminal_group_id"]:
        return _resolved["org_id"], _resolved["terminal_group_id"]

    org_id = IIKO_ORGANIZATION_ID
    if not org_id:
        orgs = await get_reserve_organizations()
        if not orgs:
            return None, None
        org_id = orgs[0]["id"]

    tg_id = IIKO_TERMINAL_GROUP_ID
    if not tg_id:
        data = await _post("/api/1/reserve/available_terminal_groups",
                           {"organizationIds": [org_id]})
        if not data or "_error" in data:
            return org_id, None
        groups = []
        for block in data.get("terminalGroups") or []:
            groups.extend(block.get("items") or [])
        if not groups:
            return org_id, None
        tg_id = groups[0]["id"]

    _resolved["org_id"] = org_id
    _resolved["terminal_group_id"] = tg_id
    return org_id, tg_id


async def get_restaurant_sections() -> list:
    """Залы и столы с GUID (для привязки к столам Mini App через /iiko_map)."""
    _, tg_id = await resolve_org_and_terminal()
    if not tg_id:
        return []
    data = await _post("/api/1/reserve/available_restaurant_sections",
                       {"terminalGroupIds": [tg_id], "returnSchema": False})
    if not data or "_error" in data:
        return []
    return data.get("restaurantSections") or []


async def get_sections_workload(section_ids: list, date_from: datetime) -> list:
    """Существующие резервы по залам (занятость столов)."""
    data = await _post("/api/1/reserve/restaurant_sections_workload",
                       {"restaurantSectionIds": section_ids,
                        "dateFrom": format_iiko_dt(date_from)})
    if not data or "_error" in data:
        return []
    return data.get("reserves") or []


async def create_reserve(table_ids: list, start: datetime, guests: int,
                         name: str, phone: str,
                         comment: str = "") -> tuple[str | None, str | None]:
    """Создать резерв. Возвращает (reserve_id, None) или (None, текст ошибки)."""
    org_id, tg_id = await resolve_org_and_terminal()
    if not org_id or not tg_id:
        return None, "не удалось определить организацию/терминал iiko"
    payload = build_reserve_payload(org_id, tg_id, table_ids, start,
                                    guests, name, phone, comment)
    data = await _post("/api/1/reserve/create", payload)
    if not data:
        return None, "нет ответа от iiko (токен?)"
    if "_error" in data:
        return None, data["_error"]
    info = data.get("reserveInfo") or {}
    reserve_id = info.get("id")
    if not reserve_id:
        return None, f"iiko не вернул id резерва: {json.dumps(data)[:200]}"
    return reserve_id, None


async def reserve_status(reserve_id: str) -> dict | None:
    """Статус доставки резерва на терминал (Success / InProgress / Error)."""
    org_id, _ = await resolve_org_and_terminal()
    if not org_id:
        return None
    data = await _post("/api/1/reserve/status_by_id",
                       {"organizationId": org_id, "reserveIds": [reserve_id]})
    if not data or "_error" in data:
        return None
    reserves = data.get("reserves") or []
    return reserves[0] if reserves else None
