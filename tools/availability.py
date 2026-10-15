
from datetime import date
from decimal import Decimal

import mysql.connector

from database.db import get_connection


def check_room_availability(
    check_in,
    check_out,
    bed_type,
    breakfast,
    guests=1,
):
    """Check room availability and return exact matches or alternatives."""

    # 1. Validate dates
    try:
        if isinstance(check_in, str):
            check_in = date.fromisoformat(check_in)

        if isinstance(check_out, str):
            check_out = date.fromisoformat(check_out)

        if not isinstance(check_in, date) or not isinstance(check_out, date):
            raise ValueError("Dates must be date objects or ISO strings.")

    except (ValueError, TypeError):
        return {
            "status": "invalid_dates",
            "rooms": [],
            "message": "Please provide valid dates in YYYY-MM-DD format.",
        }

    if check_in < date.today():
        return {
            "status": "invalid_dates",
            "rooms": [],
            "message": "Check-in cannot be in the past.",
        }

    if check_out <= check_in:
        return {
            "status": "invalid_dates",
            "rooms": [],
            "message": "Check-out must be after check-in.",
        }

    # 2. Validate preferences
    if bed_type not in ("Single", "Double"):
        return {
            "status": "invalid_preferences",
            "rooms": [],
            "message": "Bed type must be Single or Double.",
        }

    if not isinstance(breakfast, bool):
        return {
            "status": "invalid_preferences",
            "rooms": [],
            "message": "Breakfast must be True or False.",
        }

    if isinstance(guests, bool) or not isinstance(guests, int) or guests < 1:
        return {
            "status": "invalid_preferences",
            "rooms": [],
            "message": "Guest count must be a positive integer.",
        }

    nights = (check_out - check_in).days

    # 3. Find active rooms that can accommodate the guests
    # and have no overlapping confirmed bookings.
    query = """
        SELECT
            r.room_no,
            r.room_type,
            r.bed_type,
            r.breakfast,
            r.price_per_night,
            r.floor,
            r.max_guests
        FROM rooms AS r
        WHERE r.status = 'Active'
          AND r.max_guests >= %s
          AND NOT EXISTS (
              SELECT 1
              FROM bookings AS b
              WHERE b.room_no = r.room_no
                AND b.status = 'Confirmed'
                AND b.check_in < %s
                AND b.check_out > %s
          )
        ORDER BY r.price_per_night ASC, r.room_no ASC
    """

    capacity_query = """
        SELECT MAX(max_guests) AS maximum_capacity
        FROM rooms
        WHERE status = 'Active'
    """

    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(query, (guests, check_out, check_in))
        available_rooms = cursor.fetchall()

        # If no suitable rooms were returned, check hotel capacity.
        maximum_capacity = None

        if not available_rooms:
            cursor.execute(capacity_query)
            capacity_result = cursor.fetchone()
            maximum_capacity = capacity_result["maximum_capacity"]

    except mysql.connector.Error:
        return {
            "status": "database_error",
            "rooms": [],
            "nights": nights,
            "message": (
                "I couldn't check room availability right now. "
                "Please try again shortly."
            ),
        }

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()

    # 4. Convert database values into consistent Python values
    for room in available_rooms:
        room["breakfast"] = bool(room["breakfast"])
        room["price_per_night"] = float(
            room["price_per_night"]
        )
        room["total_price"] = round(
            room["price_per_night"] * nights, 2
        )

    # 5. Return exact matches first
    exact_matches = [
        room for room in available_rooms
        if room["bed_type"] == bed_type
        and room["breakfast"] == breakfast
    ]

    if exact_matches:
        return {
            "status": "exact_match",
            "rooms": exact_matches,
            "nights": nights,
            "message": f"Found {len(exact_matches)} exact match(es).",
        }

    # 6. Suggest the closest available alternatives
    def alternative_score(room):
        return (
            int(room["bed_type"] != bed_type),
            int(room["breakfast"] != breakfast),
            room["price_per_night"],
            room["room_no"],
        )

    alternatives = sorted(
        available_rooms,
        key=alternative_score,
    )[:2]

    if alternatives:
        return {
            "status": "alternatives",
            "rooms": alternatives,
            "nights": nights,
            "message": (
                "No exact match was available. "
                "Here are the closest alternatives."
            ),
        }

    # 7. Explain why no rooms are available
    if maximum_capacity is None:
        message = "The hotel currently has no active rooms."

    elif maximum_capacity < guests:
        message = (
            f"Sorry, this hotel cannot accommodate {guests} guests "
            "in a single room. Please reduce the guest count "
            "or contact the hotel about multiple rooms."
        )

    else:
        message = (
            "Sorry, no suitable rooms are available for "
            "your requested dates and guest count."
        )

    return {
        "status": "unavailable",
        "rooms": [],
        "nights": nights,
        "message": message,
    }
