
from datetime import date, timedelta

from tools.availability import check_room_availability


def test_exact_match():
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=2)

    result = check_room_availability(
        start, end, "Single", False, 1
    )

    assert result["status"] == "exact_match"
    assert len(result["rooms"]) > 0
    assert all(
        room["bed_type"] == "Single"
        and room["breakfast"] is False
        for room in result["rooms"]
    )


def test_invalid_date_range():
    start = date.today() + timedelta(days=30)
    end = start

    result = check_room_availability(
        start, end, "Single", False, 1
    )

    assert result["status"] == "invalid_dates"


def test_invalid_bed_type():
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=1)

    result = check_room_availability(
        start, end, "King", False, 1
    )

    assert result["status"] == "invalid_preferences"


def test_total_price():
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=3)

    result = check_room_availability(
        start, end, "Single", False, 1
    )

    assert result["status"] == "exact_match"

    for room in result["rooms"]:
        assert room["total_price"] == (
            room["price_per_night"] * 3
        )




def test_alternatives_when_exact_match_unavailable():
    start = date.today() + timedelta(days=60)
    end = start + timedelta(days=2)

    # Single-bed rooms in our sample data accommodate
    # only one guest. Requesting two guests forces
    # the tool to look for suitable alternatives.
    result = check_room_availability(
        start, end, "Single", False, 2
    )

    assert result["status"] == "alternatives"
    assert 1 <= len(result["rooms"]) <= 2

    # Alternatives must accommodate both guests.
    assert all(
        room["max_guests"] >= 2
        for room in result["rooms"]
    )


def test_unavailable_when_guest_capacity_exceeded():
    start = date.today() + timedelta(days=60)
    end = start + timedelta(days=2)

    # Our seeded hotel rooms accommodate at most
    # two guests per room.
    result = check_room_availability(
        start, end, "Double", True, 3
    )

    assert result["status"] == "unavailable"
    assert result["rooms"] == []
