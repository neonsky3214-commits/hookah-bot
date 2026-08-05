from datetime import datetime

from iiko import (build_reserve_payload, format_iiko_dt, norm_phone,
                  parse_booking_datetime, parse_iiko_dt,
                  tables_taken_from_reserves)

NOW = datetime(2026, 8, 5, 12, 0)


def test_parse_date_same_year():
    assert parse_booking_datetime("7 авг", "19:00", NOW) == datetime(2026, 8, 7, 19, 0)


def test_parse_date_today():
    assert parse_booking_datetime("5 авг", "21:30", NOW) == datetime(2026, 8, 5, 21, 30)


def test_parse_date_rolls_to_next_year():
    # в конце декабря бронь «2 янв» — это уже следующий год
    now = datetime(2026, 12, 30, 12, 0)
    assert parse_booking_datetime("2 янв", "18:00", now) == datetime(2027, 1, 2, 18, 0)


def test_parse_date_garbage():
    assert parse_booking_datetime("—", "—", NOW) is None
    assert parse_booking_datetime("5 xyz", "19:00", NOW) is None
    assert parse_booking_datetime("5 авг", "пусто", NOW) is None


def test_format_iiko_dt():
    assert format_iiko_dt(datetime(2026, 8, 7, 19, 0)) == "2026-08-07 19:00:00.000"


def test_norm_phone():
    assert norm_phone("8 (999) 123-45-67") == "+79991234567"
    assert norm_phone("79991234567") == "+79991234567"
    assert norm_phone("+79991234567") == "+79991234567"
    assert norm_phone("9991234567") == "+9991234567"
    assert norm_phone("") == ""


def test_build_reserve_payload():
    p = build_reserve_payload(
        "org-1", "tg-1", ["table-guid"], datetime(2026, 8, 7, 19, 0),
        guests=4, name="Иван", phone="89991234567",
        comment="Бронь из бота", duration_min=90)
    assert p["organizationId"] == "org-1"
    assert p["terminalGroupId"] == "tg-1"
    assert p["tableIds"] == ["table-guid"]
    assert p["estimatedStartTime"] == "2026-08-07 19:00:00.000"
    assert p["guestsCount"] == 4
    assert p["customer"]["name"] == "Иван"
    assert p["phone"] == "+79991234567"
    assert p["durationInMinutes"] == 90


def test_build_reserve_payload_defaults():
    p = build_reserve_payload("o", "t", ["x"], datetime(2026, 1, 1, 12, 0),
                              guests=0, name="", phone="")
    assert p["guestsCount"] == 1
    assert p["customer"]["name"] == "Гость"
    assert p["durationInMinutes"] == 120


def test_parse_iiko_dt():
    assert parse_iiko_dt("2026-08-07 19:00:00.000") == datetime(2026, 8, 7, 19, 0)
    assert parse_iiko_dt("2026-08-07T19:00:00") == datetime(2026, 8, 7, 19, 0)
    assert parse_iiko_dt("мусор") is None
    assert parse_iiko_dt("") is None


DAY = datetime(2026, 8, 7)
ID_TO_NUM = {"guid-3": 3, "guid-5": 5}
RESERVES = [
    {"estimatedStartTime": "2026-08-07 19:00:00.000", "durationInMinutes": 120,
     "tableIds": ["guid-3"]},
    {"estimatedStartTime": "2026-08-07 12:00:00.000", "durationInMinutes": 60,
     "tableIds": ["guid-5"]},
    {"estimatedStartTime": "2026-08-08 19:00:00.000", "durationInMinutes": 120,
     "tableIds": ["guid-3"]},                       # другой день
    {"estimatedStartTime": "2026-08-07 19:00:00.000", "durationInMinutes": 120,
     "tableIds": ["guid-unknown"]},                 # стол без привязки
]


def test_workload_whole_day():
    assert tables_taken_from_reserves(RESERVES, ID_TO_NUM, DAY) == {3, 5}


def test_workload_overlapping_slot():
    # 20:00 попадает в резерв 19:00–21:00 (стол 3), но не в 12:00–13:00 (стол 5)
    assert tables_taken_from_reserves(RESERVES, ID_TO_NUM, DAY, 20 * 60) == {3}


def test_workload_free_slot():
    assert tables_taken_from_reserves(RESERVES, ID_TO_NUM, DAY, 15 * 60) == set()


def test_workload_slot_boundaries():
    # начало включительно, конец исключительно
    assert tables_taken_from_reserves(RESERVES, ID_TO_NUM, DAY, 19 * 60) == {3}
    assert tables_taken_from_reserves(RESERVES, ID_TO_NUM, DAY, 21 * 60) == set()
