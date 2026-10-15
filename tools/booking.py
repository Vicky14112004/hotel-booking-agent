
import re
import secrets
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from database.db import get_connection


def confirm_booking(
    room_no,
    check_in,
    check_out,
    guest_name,
    guest_email,
    guests,
    bed_type,
    breakfast,
):
    """Validate and save a hotel reservation in MySQL."""

    # 1. Validate dates
    try:
        if isinstance(check_in, str):
            check_in = date.fromisoformat(check_in)
        if isinstance(check_out, str):
            check_out = date.fromisoformat(check_out)
    except (ValueError, TypeError):
        return {
            "status": "invalid_dates",
            "message": "Please provide valid dates in YYYY-MM-DD format.",
        }

    if check_in < date.today() or check_out <= check_in:
        return {
            "status": "invalid_dates",
            "message": "Please provide a valid future stay.",
        }

    # 2. Validate guest information
    if not isinstance(guest_name, str) or not guest_name.strip():
        return {
            "status": "invalid_guest",
            "message": "Please provide the guest's name.",
        }

    guest_name = guest_name.strip()

    if len(guest_name) > 100:
        return {
            "status": "invalid_guest",
            "message": "The guest name must be 100 characters or fewer.",
        }

    if not isinstance(guest_email, str):
        return {
            "status": "invalid_guest",
            "message": "Please provide a valid email address.",
        }

    guest_email = guest_email.strip()

    if (
        len(guest_email) > 255
        or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            guest_email,
        )
    ):
        return {
            "status": "invalid_guest",
            "message": "Please provide a valid email address.",
        }

    if (
        isinstance(guests, bool)
        or not isinstance(guests, int)
        or guests < 1
    ):
        return {
            "status": "invalid_preferences",
            "message": "Guest count must be a positive integer.",
        }

    if (
        isinstance(room_no, bool)
        or not isinstance(room_no, int)
        or room_no < 1
        or bed_type not in ("Single", "Double")
        or not isinstance(breakfast, bool)
    ):
        return {
            "status": "invalid_preferences",
            "message": "Please check the selected room and preferences.",
        }

    nights = (check_out - check_in).days
    connection = None
    cursor = None

    try:
        connection = get_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)

        # 3. Lock the room row so competing booking requests
        # for the same room are serialized.
        cursor.execute(
            """
            SELECT room_no, room_type, bed_type, breakfast,
                   price_per_night, max_guests, status
            FROM rooms
            WHERE room_no = %s
            FOR UPDATE
            """,
            (room_no,),
        )
        room = cursor.fetchone()

        if room is None:
            connection.rollback()
            return {
                "status": "room_not_found",
                "message": "The selected room does not exist.",
            }

        # 4. Verify the room still satisfies the request
        if (
            room["status"] != "Active"
            or room["max_guests"] < guests
            or room["bed_type"] != bed_type
            or bool(room["breakfast"]) != breakfast
        ):
            connection.rollback()
            return {
                "status": "room_unavailable",
                "message": (
                    "The selected room no longer matches "
                    "your requirements. Please search again."
                ),
            }

        # 5. Recheck overlapping confirmed reservations
        cursor.execute(
            """
            SELECT booking_id
            FROM bookings
            WHERE room_no = %s
              AND status = 'Confirmed'
              AND check_in < %s
              AND check_out > %s
            LIMIT 1
            FOR UPDATE
            """,
            (room_no, check_out, check_in),
        )

        if cursor.fetchone():
            connection.rollback()
            return {
                "status": "room_unavailable",
                "message": (
                    "Sorry, this room has just been booked "
                    "for overlapping dates. Please search again."
                ),
            }

        # 6. Calculate price using the database rate
        price_per_night = Decimal(str(room["price_per_night"]))
        total_price = (
            price_per_night * nights
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 7. Create a unique booking reference
        booking_ref = "HTL-" + secrets.token_hex(5).upper()

        cursor.execute(
            """
            INSERT INTO bookings (
                booking_ref, room_no, check_in, check_out,
                guest_name, guest_email, guests, total_price,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Confirmed')
            """,
            (
                booking_ref,
                room_no,
                check_in,
                check_out,
                guest_name,
                guest_email,
                guests,
                total_price,
            ),
        )

        connection.commit()

        # 8. Return confirmation only after commit succeeds
        return {
            "status": "confirmed",
            "booking_ref": booking_ref,
            "room_no": room["room_no"],
            "room_type": room["room_type"],
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "nights": nights,
            "guest_name": guest_name,
            "guest_email": guest_email,
            "guests": guests,
            "bed_type": room["bed_type"],
            "breakfast": bool(room["breakfast"]),
            "price_per_night": float(price_per_night),
            "total_price": float(total_price),
            "message": "Your booking has been confirmed.",
        }

    except Exception:
        if connection is not None:
            connection.rollback()

        # Log the exception on the server in a later
        # production-ready logging layer.
        return {
            "status": "booking_error",
            "message": (
                "We could not complete the booking. "
                "Please try again."
            ),
        }

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
