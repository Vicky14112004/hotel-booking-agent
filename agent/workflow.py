
import logging
import re
from datetime import date, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from agent.state import HotelBookingState
from tools.availability import check_room_availability
from tools.booking import confirm_booking
from services.email_service import create_confirmation_email


logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS AND REGEX
# ============================================================

EMAIL_PATTERN = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
)

EMAIL_SEARCH_PATTERN = re.compile(
    r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+"
)

DATE_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"
)

ROOM_PATTERN = re.compile(
    r"\b(?:room\s*)#?\s*(\d{3})\b",
    re.IGNORECASE,
)

GUEST_COUNT_PATTERN = re.compile(
    r"\b(\d+)\s*(?:guests?|people|persons?)\b",
    re.IGNORECASE,
)

YES_ANSWERS = {
    "yes", "y", "yeah", "yep", "sure", "correct",
    "absolutely", "please do", "confirm", "confirmed",
}

NO_ANSWERS = {
    "no", "n", "nope", "nah", "not now",
}

PREFERENCE_WORDS = {
    "change", "instead", "different dates", "another date",
    "breakfast", "bed", "single", "double", "guests",
    "people", "check-in", "check in", "check-out", "check out",
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def _clean(value: Any) -> str:
    """Convert a value to a trimmed string."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(value: str) -> date | None:
    """Parse supported date formats into a date."""
    value = value.strip()

    formats = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d-%m-%y",
        "%d/%m/%y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def _parse_dates(message: str) -> list[date]:
    """Extract valid date values from a message."""
    parsed = []

    for match in DATE_PATTERN.finditer(message):
        value = _parse_date(match.group(0))

        if value is not None and value not in parsed:
            parsed.append(value)

    return parsed


def _extract_email(message: str) -> str | None:
    """Extract a valid-looking email address."""
    match = EMAIL_SEARCH_PATTERN.search(message)

    if not match:
        return None

    email = match.group(0).strip(".,;:!?")

    if EMAIL_PATTERN.fullmatch(email):
        return email

    return None


def _extract_name_before_email(message: str) -> str | None:
    """Extract a likely name from 'Harshitha, name@example.com'."""
    email = _extract_email(message)

    if not email:
        return None

    name = message.replace(email, "").strip(" \t,;:-")

    # Remove common introductory phrases.
    name = re.sub(
        r"^(?:my name is|name is|guest name is)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()

    if not name or "@" in name or len(name) > 100:
        return None

    # Avoid treating an instruction as the guest's name.
    if len(name.split()) > 8:
        return None

    return name


def _extract_explicit_name(message: str) -> str | None:
    """Extract a name introduced explicitly by the guest."""
    patterns = (
        r"\bmy name is\s+(.+)",
        r"\bguest name is\s+(.+)",
        r"\bname is\s+(.+)",
    )

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)

        if match:
            value = match.group(1).strip(" \t,;.")

            email = _extract_email(value)
            if email:
                value = value.replace(email, "").strip(" \t,;:-")

            if value and len(value) <= 100:
                return value

    return None


def _is_yes(message: str) -> bool:
    return message.strip().lower().rstrip(".!?") in YES_ANSWERS


def _is_no(message: str) -> bool:
    return message.strip().lower().rstrip(".!?") in NO_ANSWERS


def _is_explicit_confirmation(message: str) -> bool:
    """Check for a clear confirmation rather than any positive sentence."""
    text = message.strip().lower().rstrip(".!?")

    if text in YES_ANSWERS:
        return True

    return bool(
        re.search(
            r"\b(confirm the booking|book it|proceed with booking|"
            r"yes,? confirm|go ahead and book)\b",
            text,
        )
    )


def _is_explicit_decline(message: str) -> bool:
    text = message.strip().lower().rstrip(".!?")

    if text in NO_ANSWERS:
        return True

    return bool(
        re.search(
            r"\b(cancel the booking|do not book|don't book|"
            r"no,? cancel|stop the booking)\b",
            text,
        )
    )


def _clear_booking_data() -> dict[str, Any]:
    """Fields invalidated when a new search is requested."""
    return {
        "requested_room_no": None,
        "booking_result": None,
        "email_result": None,
        "awaiting_confirmation": False,
        "booking_confirmed_by_guest": False,
    }


def _add_human_message(state: HotelBookingState) -> list:
    """Append the current user message without losing earlier messages."""
    messages = list(state.get("messages") or [])
    user_message = _clean(state.get("user_message"))

    if user_message:
        messages.append(HumanMessage(content=user_message))

    return messages


def _format_price(value: Any) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (ValueError, TypeError):
        return "Price unavailable"


# ============================================================
# MESSAGE INTERPRETATION
# ============================================================

def interpret_message(state: HotelBookingState) -> dict[str, Any]:
    """
    Interpret the user's reply using the explicit conversation step.

    Deterministic parsing is used for booking-critical fields so that
    the flow does not depend on Gemini being available.
    """
    message = _clean(state.get("user_message"))

    if not message:
        return {
            "response": (
                "Welcome to Grand Credang! I can help you find a room. "
                "What are your check-in and check-out dates?"
            ),
            "current_step": "collect_preferences",
            "intent": "other",
        }

    text = message.lower().strip()
    step = state.get("current_step") or "collect_preferences"
    
    # Handle messages after a booking is completed.
    if step == "booking_complete":
        text = message.lower().strip()

        new_booking_phrases = (
            "new booking",
            "another booking",
            "book another room",
            "book a room",
            "new search",
            "search rooms",
            "find a room",
            "show available rooms",
        )

        has_new_booking_intent = any(
            phrase in text for phrase in new_booking_phrases
        )

        has_new_preferences = bool(
            _parse_dates(message)
            or re.search(r"\b(single|double)\b", text)
            or GUEST_COUNT_PATTERN.search(message)
            or re.search(
                r"\b(with breakfast|without breakfast|"
                r"no breakfast|include breakfast|"
                r"don't want breakfast|dont want breakfast)\b",
                text,
            )
        )

        if has_new_booking_intent or has_new_preferences:
            return {
                "messages": _add_human_message(state),
                "user_message": message,
                "current_step": "collect_preferences",
                "intent": "search",
                "check_in": None,
                "check_out": None,
                "bed_type": None,
                "breakfast": None,
                "guests": None,
                "availability_result": None,
                "requested_room_no": None,
                "guest_name": None,
                "guest_email": None,
                "awaiting_confirmation": False,
                "booking_confirmed_by_guest": False,
                "pending_price_question": False,
                "response": (
                    "Of course! Let's start a new booking. "
                    "What are your check-in and check-out dates?"
                ),
            }

        if any(
            word in text
            for word in (
                "price", "cost", "costly",
                "expensive", "cheapest",
            )
        ):
            return {
                "messages": _add_human_message(state),
                "user_message": message,
                "intent": "other",
                "current_step": "booking_complete",
                "response": (
                    "Your previous booking is confirmed. "
                    "Would you like to compare room prices in a new search, "
                    "make another booking, or ask something else?"
                ),
            }

        return {
            "messages": _add_human_message(state),
            "user_message": message,
            "intent": "other",
            "current_step": "booking_complete",
            "response": (
                "Your booking is confirmed! I'm ready to help with "
                "another booking, room availability, prices, "
                "or other hotel-related questions."
            ),
        }


    updates: dict[str, Any] = {
        "messages": _add_human_message(state),
        "user_message": message,
    }

    # --------------------------------------------------------
    # 1. Handle explicit booking confirmation first.
    # --------------------------------------------------------
    if step == "awaiting_confirmation":
        if _is_explicit_confirmation(message):
            return {
                **updates,
                "intent": "confirm_booking",
                "booking_confirmed_by_guest": True,
                "awaiting_confirmation": True,
                "response": "Thank you. I'm processing your booking.",
            }

        if _is_explicit_decline(message):
            return {
                **updates,
                "intent": "cancel_confirmation",
                "booking_confirmed_by_guest": False,
                "awaiting_confirmation": False,
                "current_step": "cancelled",
                "response": (
                    "No booking has been made. "
                    "Would you like to search for another room?"
                ),
            }

        return {
            **updates,
            "intent": "other",
            "response": (
                "Please reply yes to confirm this booking, "
                "or no to cancel it."
            ),
        }

    # --------------------------------------------------------
    # 2. Handle breakfast yes/no only when expected.
    # --------------------------------------------------------
    if step == "collect_breakfast":
        if _is_yes(message):
            return {
                **updates,
                "breakfast": True,
                "intent": "search",
            }

        if _is_no(message):
            return {
                **updates,
                "breakfast": False,
                "intent": "search",
            }

    # --------------------------------------------------------
    # 3. Extract common booking fields.
    # --------------------------------------------------------
    dates = _parse_dates(message)

    if len(dates) >= 2:
        updates["check_in"] = dates[0].isoformat()
        updates["check_out"] = dates[1].isoformat()

    elif len(dates) == 1:
        parsed_date = dates[0]

        if re.search(
            r"\b(check[- ]?in|arrival|arrive)\b",
            text,
        ):
            updates["check_in"] = parsed_date.isoformat()

        elif re.search(
            r"\b(check[- ]?out|departure|depart)\b",
            text,
        ):
            updates["check_out"] = parsed_date.isoformat()

        elif not state.get("check_in"):
            updates["check_in"] = parsed_date.isoformat()

        elif not state.get("check_out"):
            updates["check_out"] = parsed_date.isoformat()

        elif step == "collect_preferences":
            # If both dates were previously set, a lone date is
            # ambiguous. Do not silently overwrite either date.
            updates["response"] = (
                "Is that your new check-in date or check-out date?"
            )
            updates["intent"] = "other"
            return updates

    bed_match = re.search(r"\b(single|double)\b", text)

    if bed_match:
        updates["bed_type"] = bed_match.group(1).capitalize()

    guest_match = GUEST_COUNT_PATTERN.search(message)

    if guest_match:
        updates["guests"] = int(guest_match.group(1))

    elif step == "collect_preferences":
        # Accept a bare number as a guest count only if it is
        # not a date or room number.
        if re.fullmatch(r"\s*[1-9]\d?\s*", message):
            updates["guests"] = int(message.strip())

    # Breakfast is parsed only from explicit preference language,
    # except for the stage-specific yes/no handling above.
    if re.search(
        r"\b(no breakfast|without breakfast|breakfast not included|"
        r"don't need breakfast|do not need breakfast)\b",
        text,
    ):
        updates["breakfast"] = False

    elif re.search(
        r"\b(with breakfast|include breakfast|breakfast included|"
        r"need breakfast|want breakfast)\b",
        text,
    ):
        updates["breakfast"] = True

    # Extract email and name without discarding previously valid values.
    email = _extract_email(message)

    if email:
        updates["guest_email"] = email

    name = _extract_explicit_name(message)

    if not name and email:
        name = _extract_name_before_email(message)

    if not name and step == "collect_guest_name" and not email:
        candidate = message.strip(" \t,;.")

        # Do not store obvious commands or preferences as a name.
        if (
            candidate
            and len(candidate) <= 100
            and not re.search(
                r"\b(room|breakfast|guest|guests|check-in|check-out|"
                r"search|price|cancel|book|single|double)\b",
                candidate,
                re.IGNORECASE,
            )
        ):
            name = candidate

    if name:
        updates["guest_name"] = name

    room_match = ROOM_PATTERN.search(message)

    if room_match:
        updates["requested_room_no"] = int(room_match.group(1))

    elif (
        step == "select_room"
        and re.fullmatch(r"\s*\d{3}\s*", message)
    ):
        updates["requested_room_no"] = int(message.strip())

    # --------------------------------------------------------
    # 4. Decide the next action using the conversation step.
    # --------------------------------------------------------

    # User is choosing a room.
    if step == "select_room":
        if updates.get("requested_room_no") is not None:
            updates["intent"] = "select_room"
            return updates

        # Preference changes invalidate the previous result.
        if any(
            key in updates
            for key in (
                "check_in", "check_out", "bed_type",
                "breakfast", "guests",
            )
        ):
            updates.update(_clear_booking_data())
            updates["availability_result"] = None
            updates["intent"] = "search"
            return updates

        if any(word in text for word in ("price", "cost", "cheapest")):
            updates["intent"] = "price"
            updates["pending_price_question"] = True
            return updates

        updates["intent"] = "other"
        updates["response"] = (
            "Please select a room number from the available rooms "
            "shown above, or tell me which preference you'd like to change."
        )
        return updates

    # User is entering guest details.
    if step in {"collect_guest_name", "collect_guest_email"}:
        updates["intent"] = "provide_details"
        return updates

    # User is choosing a breakfast option, but entered something
    # other than yes/no.
    if step == "collect_breakfast" and "breakfast" not in updates:
        if any(word in text for word in ("price", "cost", "cheapest")):
            updates["intent"] = "price"
            updates["pending_price_question"] = True
            return updates

        updates["intent"] = "other"
        updates["response"] = (
            "Would you like breakfast included? Please answer yes or no."
        )
        return updates

    # Explicit search or preference-change intent.
    preference_changed = any(
        key in updates
        for key in (
            "check_in", "check_out", "bed_type",
            "breakfast", "guests",
        )
    )

    if preference_changed:
        updates.update(_clear_booking_data())
        updates["availability_result"] = None
        updates["intent"] = "search"
        return updates

    if any(word in text for word in ("price", "cost", "cheapest")):
        updates["intent"] = "price"
        updates["pending_price_question"] = True
        return updates

    if any(
        phrase in text
        for phrase in (
            "search rooms", "find a room", "show rooms",
            "available rooms", "availability",
            "start again", "new search",
        )
    ):
        updates.update(_clear_booking_data())
        updates["availability_result"] = None
        updates["intent"] = "search"
        return updates

    # Handle bare yes/no only for a step that expects a decision.
    if _is_yes(message) or _is_no(message):
        if step == "collect_preferences":
            updates["intent"] = "other"
            updates["response"] = (
                "Please tell me your check-in date, check-out date, "
                "bed type, breakfast preference, and number of guests."
            )
            return updates

    # Initial or general conversation.
    if not state.get("check_in") or not state.get("check_out"):
        updates["intent"] = "search"
        return updates

    updates["intent"] = "other"
    updates["response"] = (
        "I can help with room availability, room prices, or booking. "
        "What would you like to do next?"
    )
    return updates


# ============================================================
# AVAILABILITY SEARCH
# ============================================================

def validate_and_search(state: HotelBookingState) -> dict[str, Any]:
    """Validate required preferences and search the database."""
    check_in = _clean(state.get("check_in"))
    check_out = _clean(state.get("check_out"))
    bed_type = state.get("bed_type")
    breakfast = state.get("breakfast")
    guests = state.get("guests")

    missing = []

    if not check_in:
        missing.append("check-in date")

    if not check_out:
        missing.append("check-out date")

    if bed_type not in {"Single", "Double"}:
        missing.append("bed type (Single or Double)")

    if not isinstance(breakfast, bool):
        missing.append("breakfast preference (yes or no)")

    if (
        isinstance(guests, bool)
        or not isinstance(guests, int)
        or guests < 1
    ):
        missing.append("number of guests")

    if missing:
        first_missing = missing[0]

        if first_missing == "check-in date":
            response = "What is your check-in date?"
            step = "collect_preferences"

        elif first_missing == "check-out date":
            response = "What is your check-out date?"
            step = "collect_preferences"

        elif first_missing.startswith("bed type"):
            response = "Would you prefer a Single or Double bed?"
            step = "collect_preferences"

        elif first_missing.startswith("breakfast"):
            response = "Would you like breakfast included? Please answer yes or no."
            step = "collect_breakfast"

        else:
            response = "How many guests will be staying?"
            step = "collect_preferences"

        return {
            "response": response,
            "current_step": step,
            "intent": "collect_preferences",
        }

    parsed_check_in = _parse_date(check_in)
    parsed_check_out = _parse_date(check_out)

    if parsed_check_in is None or parsed_check_out is None:
        return {
            "response": "Please provide dates in YYYY-MM-DD format.",
            "current_step": "collect_preferences",
            "intent": "collect_preferences",
        }

    if parsed_check_in < date.today():
        return {
            "response": "Check-in cannot be in the past. What date would you like instead?",
            "current_step": "collect_preferences",
            "intent": "collect_preferences",
        }

    if parsed_check_out <= parsed_check_in:
        return {
            "response": "Check-out must be after check-in. Please provide a valid check-out date.",
            "current_step": "collect_preferences",
            "intent": "collect_preferences",
        }

    try:
        result = check_room_availability(
            check_in=parsed_check_in.isoformat(),
            check_out=parsed_check_out.isoformat(),
            bed_type=bed_type,
            breakfast=breakfast,
            guests=guests,
        )
    except Exception:
        logger.exception("Room availability search failed")
        return {
            "response": (
                "I couldn't check room availability right now. "
                "Please try again shortly."
            ),
            "current_step": "error",
            "intent": "availability_error",
        }

    result = dict(result or {})
    result["request"] = {
        "check_in": parsed_check_in.isoformat(),
        "check_out": parsed_check_out.isoformat(),
        "bed_type": bed_type,
        "breakfast": breakfast,
        "guests": guests,
    }

    status = result.get("status")
    rooms = result.get("rooms") or []

    if status == "exact_match" and rooms:
        lines = [
            f"Found {len(rooms)} matching room(s) for "
            f"{result.get('nights', 0)} night(s):"
        ]

        for room in rooms:
            lines.append(
                f"Room {room['room_no']} — {room.get('room_type', 'Room')}, "
                f"{room['bed_type']} bed, "
                f"breakfast {'included' if room['breakfast'] else 'not included'}, "
                f"{_format_price(room['price_per_night'])} per night, "
                f"total {_format_price(room['total_price'])}."
            )

        lines.append("Which room would you like to book?")
        response = "\n".join(lines)

        return {
            "availability_result": result,
            "requested_room_no": None,
            "booking_result": None,
            "email_result": None,
            "awaiting_confirmation": False,
            "booking_confirmed_by_guest": False,
            "current_step": "select_room",
            "intent": "availability_result",
            "response": response,
            "pending_price_question": False,
        }

    if status == "alternatives" and rooms:
        lines = [
            "No exact match is available, but these alternatives may work:"
        ]

        for room in rooms[:2]:
            lines.append(
                f"Room {room['room_no']} — {room.get('room_type', 'Room')}, "
                f"{room['bed_type']} bed, "
                f"breakfast {'included' if room['breakfast'] else 'not included'}, "
                f"{_format_price(room['price_per_night'])} per night, "
                f"total {_format_price(room['total_price'])}."
            )

        lines.append("Would you like one of these rooms, or would you prefer to change your requirements?")
        response = "\n".join(lines)

        return {
            "availability_result": result,
            "requested_room_no": None,
            "booking_result": None,
            "email_result": None,
            "awaiting_confirmation": False,
            "booking_confirmed_by_guest": False,
            "current_step": "select_room",
            "intent": "availability_result",
            "response": response,
            "pending_price_question": False,
        }

    message = result.get(
        "message",
        "No suitable rooms are available for those preferences.",
    )

    return {
        "availability_result": result,
        "requested_room_no": None,
        "current_step": "collect_preferences",
        "intent": "availability_result",
        "response": message + " You can try different dates or preferences.",
        "pending_price_question": False,
    }


# ============================================================
# ROOM SELECTION
# ============================================================

def handle_room_selection(state: HotelBookingState) -> dict[str, Any]:
    """Validate selection against the latest availability result."""
    room_no = state.get("requested_room_no")
    availability = state.get("availability_result") or {}
    rooms = availability.get("rooms") or []

    if not isinstance(room_no, int) or isinstance(room_no, bool):
        return {
            "response": "Please choose a valid room number from the available rooms.",
            "current_step": "select_room",
            "intent": "select_room",
        }

    if availability.get("status") not in {"exact_match", "alternatives"}:
        return {
            "response": "Your previous room list is no longer valid. Let me search again.",
            "intent": "search",
            "availability_result": None,
        }

    selected = next(
        (
            room for room in rooms
            if int(room.get("room_no", -1)) == room_no
        ),
        None,
    )

    if selected is None:
        return {
            "response": (
                "That room isn't in the latest available-room list. "
                "Please select one of the rooms shown."
            ),
            "current_step": "select_room",
            "intent": "select_room",
            "requested_room_no": None,
        }

    guests = state.get("guests")

    if (
        isinstance(guests, bool)
        or not isinstance(guests, int)
        or guests < 1
        or int(selected.get("max_guests", 0)) < guests
    ):
        return {
            "response": (
                "That room cannot accommodate your guest count. "
                "Please choose another available room."
            ),
            "current_step": "select_room",
            "intent": "select_room",
            "requested_room_no": None,
        }

    # If an alternative was chosen, update the search preferences to
    # match that room and refresh availability before proceeding.
    selected_bed = selected.get("bed_type")
    selected_breakfast = bool(selected.get("breakfast"))

    if (
        availability.get("status") == "alternatives"
        and (
            selected_bed != state.get("bed_type")
            or selected_breakfast != state.get("breakfast")
        )
    ):
        try:
            refreshed = check_room_availability(
                check_in=state.get("check_in"),
                check_out=state.get("check_out"),
                bed_type=selected_bed,
                breakfast=selected_breakfast,
                guests=guests,
            )
        except Exception:
            logger.exception("Alternative-room refresh failed")
            return {
                "response": (
                    "I couldn't verify that alternative just now. "
                    "Please try selecting a room again."
                ),
                "current_step": "select_room",
                "intent": "select_room",
                "requested_room_no": None,
            }

        refreshed = dict(refreshed or {})
        refreshed["request"] = {
            "check_in": state.get("check_in"),
            "check_out": state.get("check_out"),
            "bed_type": selected_bed,
            "breakfast": selected_breakfast,
            "guests": guests,
        }

        refreshed_rooms = refreshed.get("rooms") or []
        still_available = any(
            int(room.get("room_no", -1)) == room_no
            for room in refreshed_rooms
        )

        if not still_available:
            return {
                "availability_result": refreshed,
                "requested_room_no": None,
                "current_step": "select_room",
                "intent": "availability_result",
                "response": (
                    "That alternative is no longer available. "
                    "Please choose from the updated room list."
                ),
            }

        availability = refreshed

    return {
        "availability_result": availability,
        "requested_room_no": room_no,
        "bed_type": selected_bed,
        "breakfast": selected_breakfast,
        "current_step": (
            "collect_guest_email"
            if state.get("guest_name")
            else "collect_guest_name"
        ),
        "intent": "provide_details",
        "awaiting_confirmation": False,
        "booking_confirmed_by_guest": False,
        "response": (
            f"You selected Room {room_no}. "
            f"It costs {_format_price(selected.get('price_per_night'))} per night "
            f"for {availability.get('nights', 0)} night(s), "
            f"with a total of {_format_price(selected.get('total_price'))}. "
            + (
                "Please provide your email address."
                if state.get("guest_name")
                else "Please provide the guest's full name and email address."
            )
        ),
    }


# ============================================================
# GUEST DETAILS AND CONFIRMATION
# ============================================================

def collect_details(state: HotelBookingState) -> dict[str, Any]:
    """Validate guest information and show a confirmation summary."""
    name = _clean(state.get("guest_name"))
    email = _clean(state.get("guest_email"))

    if not name:
        return {
            "response": "Please provide the guest's full name.",
            "current_step": "collect_guest_name",
            "intent": "provide_details",
        }

    if len(name) > 100:
        return {
            "response": "The guest's name must be 100 characters or fewer. Please enter the name again.",
            "current_step": "collect_guest_name",
            "intent": "provide_details",
        }

    if not email:
        return {
            "response": "Please provide the guest's email address.",
            "current_step": "collect_guest_email",
            "intent": "provide_details",
        }

    if len(email) > 255 or not EMAIL_PATTERN.fullmatch(email):
        return {
            "response": "That email address doesn't look valid. Please enter a valid email address.",
            "current_step": "collect_guest_email",
            "intent": "provide_details",
        }

    availability = state.get("availability_result") or {}
    rooms = availability.get("rooms") or []
    room_no = state.get("requested_room_no")

    if availability.get("status") not in {"exact_match", "alternatives"}:
        return {
            "response": "Your room availability needs to be refreshed. Let me search again.",
            "intent": "search",
            "availability_result": None,
        }

    selected = next(
        (
            room for room in rooms
            if int(room.get("room_no", -1)) == room_no
        ),
        None,
    )

    if selected is None:
        return {
            "response": "Please select a valid room from the latest availability results.",
            "current_step": "select_room",
            "intent": "select_room",
            "requested_room_no": None,
        }

    nights = availability.get("nights", 0)
    total = selected.get("total_price")

    response = (
        "Please review your booking details:\n\n"
        f"- Guest: {name}\n"
        f"- Email: {email}\n"
        f"- Room: {room_no} ({selected.get('room_type', 'Room')})\n"
        f"- Check-in: {state.get('check_in')}\n"
        f"- Check-out: {state.get('check_out')}\n"
        f"- Nights: {nights}\n"
        f"- Guests: {state.get('guests')}\n"
        f"- Bed: {selected.get('bed_type')}\n"
        f"- Breakfast: {'Included' if selected.get('breakfast') else 'Not included'}\n"
        f"- Price per night: {_format_price(selected.get('price_per_night'))}\n"
        f"- Total: {_format_price(total)}\n\n"
        "Would you like me to confirm this booking? Please reply yes or no."
    )

    return {
        "guest_name": name,
        "guest_email": email,
        "current_step": "awaiting_confirmation",
        "intent": "awaiting_confirmation",
        "awaiting_confirmation": True,
        "booking_confirmed_by_guest": False,
        "response": response,
    }


def cancel_confirmation(state: HotelBookingState) -> dict[str, Any]:
    """Stop before booking when the guest declines confirmation."""
    return {
        "awaiting_confirmation": False,
        "booking_confirmed_by_guest": False,
        "current_step": "cancelled",
        "intent": "cancelled",
        "response": (
            "No booking has been created. "
            "You can ask me to search for another room whenever you're ready."
        ),
    }


# ============================================================
# PRICE QUESTIONS
# ============================================================

def answer_price_question(state: HotelBookingState) -> dict[str, Any]:
    """Answer pricing questions using database availability results."""
    availability = state.get("availability_result") or {}
    rooms = availability.get("rooms") or []

    # Refresh results when the stored search does not match current state.
    request = availability.get("request") or {}

    current_request = {
        "check_in": state.get("check_in"),
        "check_out": state.get("check_out"),
        "bed_type": state.get("bed_type"),
        "breakfast": state.get("breakfast"),
        "guests": state.get("guests"),
    }

    if (
        not rooms
        or availability.get("status") not in {"exact_match", "alternatives"}
        or any(request.get(key) != value for key, value in current_request.items())
    ):
        result = validate_and_search(state)

        # If the search has only asked for missing preferences,
        # return that response instead of fabricating a price.
        refreshed = result.get("availability_result") or {}

        if not refreshed.get("rooms"):
            return result

        availability = refreshed
        rooms = availability.get("rooms") or []

    if not rooms:
        return {
            "response": "There are no available rooms to price for those preferences.",
            "intent": "price",
        }

    selected_room_no = state.get("requested_room_no")
    selected = next(
        (
            room for room in rooms
            if int(room.get("room_no", -1)) == selected_room_no
        ),
        None,
    )

    if selected is None:
        selected = min(
            rooms,
            key=lambda room: float(room.get("price_per_night", 0)),
        )

    response = (
        f"Room {selected['room_no']} costs "
        f"{_format_price(selected.get('price_per_night'))} per night "
        f"for {availability.get('nights', 0)} night(s), "
        f"with a total of {_format_price(selected.get('total_price'))}. "
    )

    if selected_room_no is None:
        response += (
            "Would you like to choose this room, or would you like "
            "to compare the other available options?"
        )
    else:
        response += "Would you like to continue with this room?"

    return {
        "availability_result": availability,
        "pending_price_question": False,
        "current_step": "select_room",
        "intent": "price",
        "response": response,
    }


# ============================================================
# CREATE BOOKING
# ============================================================

def book_room(state: HotelBookingState) -> dict[str, Any]:
    """Create a booking only after explicit guest confirmation."""
    existing_booking = state.get("booking_result") or {}

    if existing_booking.get("status") == "confirmed":
        return {
            "intent": "booking_confirmed",
            "current_step": "booking_complete",
            "response": (
                f"Your booking is already confirmed. "
                f"Reference: {existing_booking.get('booking_ref')}."
            ),
        }

    if not state.get("booking_confirmed_by_guest"):
        return {
            "response": "Please confirm the booking before I proceed.",
            "intent": "awaiting_confirmation",
            "current_step": "awaiting_confirmation",
            "awaiting_confirmation": True,
        }

    availability = state.get("availability_result") or {}
    rooms = availability.get("rooms") or []
    room_no = state.get("requested_room_no")

    selected = next(
        (
            room for room in rooms
            if int(room.get("room_no", -1)) == room_no
        ),
        None,
    )

    if selected is None:
        return {
            "response": "The selected room is no longer in the current results. Please search again.",
            "intent": "search",
            "availability_result": None,
            "requested_room_no": None,
            "booking_confirmed_by_guest": False,
            "awaiting_confirmation": False,
        }

    try:
        result = confirm_booking(
            room_no=room_no,
            check_in=state.get("check_in"),
            check_out=state.get("check_out"),
            guest_name=state.get("guest_name"),
            guest_email=state.get("guest_email"),
            guests=state.get("guests"),
            bed_type=selected.get("bed_type"),
            breakfast=bool(selected.get("breakfast")),
        )
    except Exception:
        logger.exception("Booking creation failed")
        result = {
            "status": "booking_error",
            "message": "We couldn't complete your booking. Please try again.",
        }

    if result.get("status") != "confirmed":
        return {
            "booking_result": result,
            "booking_confirmed_by_guest": False,
            "awaiting_confirmation": False,
            "current_step": "select_room",
            "intent": "booking_failed",
            "response": (
                result.get("message")
                or "The booking could not be completed. Please search again."
            ),
        }

    return {
        "booking_result": result,
        "booking_confirmed_by_guest": False,
        "awaiting_confirmation": False,
        "current_step": "booking_complete",
        "intent": "booking_confirmed",
        "response": (
            "Your booking has been confirmed!\n"
            f"Booking reference: {result.get('booking_ref')}\n"
            f"Room: {result.get('room_no')}\n"
            f"Guest: {result.get('guest_name')}\n"
            f"Total: {_format_price(result.get('total_price'))}"
        ),
    }


# ============================================================
# EMAIL PREVIEW
# ============================================================

def send_confirmation_email(state: HotelBookingState) -> dict[str, Any]:
    """Generate the confirmation email preview after successful booking."""
    booking = state.get("booking_result") or {}

    if booking.get("status") != "confirmed":
        return {
            "intent": "booking_failed",
            "response": "An email preview cannot be generated because the booking is not confirmed.",
        }

    existing_email = state.get("email_result")

    if existing_email:
        return {
            "email_result": existing_email,
            "intent": "booking_confirmed",
            "response": state.get("response", "Your booking is confirmed."),
        }

    try:
        email_result = create_confirmation_email(booking)
    except Exception:
        logger.exception("Confirmation email preview creation failed")
        email_result = {
            "status": "email_preview_error",
            "message": (
                "The booking is confirmed, but the email preview "
                "could not be generated."
            ),
        }

    response = _clean(state.get("response")) or "Your booking is confirmed."

    if email_result.get("status") == "email_preview_error":
        response += "\n\nThe email preview could not be generated."

    else:
        response += (
            "\n\nA confirmation email preview has been prepared. "
            "This is a preview and does not mean an email was sent."
        )

    return {
        "email_result": email_result,
        "intent": "booking_confirmed",
        "current_step": "booking_complete",
        "response": response,
    }


# ============================================================
# ROUTING
# ============================================================

def route_after_interpretation(state: HotelBookingState) -> str:
    """Route based on the interpreted action."""
    intent = state.get("intent")

    routes = {
        "search": "search",
        "availability": "search",
        "change_preferences": "search",
        "select_room": "select",
        "provide_details": "details",
        "booking": "details",
        "confirm_booking": "book",
        "cancel_confirmation": "cancel",
        "price": "price",
    }

    return routes.get(intent, "end")


def route_after_booking(state: HotelBookingState) -> str:
    booking = state.get("booking_result") or {}

    if booking.get("status") == "confirmed":
        return "email"

    return "end"


# ============================================================
# BUILD GRAPH
# ============================================================

def build_workflow():
    """Build the LangGraph hotel booking workflow."""
    graph = StateGraph(HotelBookingState)

    graph.add_node("interpret", interpret_message)
    graph.add_node("search", validate_and_search)
    graph.add_node("select", handle_room_selection)
    graph.add_node("details", collect_details)
    graph.add_node("price", answer_price_question)
    graph.add_node("book", book_room)
    graph.add_node("email", send_confirmation_email)
    graph.add_node("cancel", cancel_confirmation)

    graph.add_edge(START, "interpret")

    graph.add_conditional_edges(
        "interpret",
        route_after_interpretation,
        {
            "search": "search",
            "select": "select",
            "details": "details",
            "book": "book",
            "price": "price",
            "cancel": "cancel",
            "end": END,
        },
    )

    # Each conversational node returns to the UI. The next user
    # message starts another graph invocation with the saved state.
    graph.add_edge("search", END)
    graph.add_edge("select", END)
    graph.add_edge("details", END)
    graph.add_edge("price", END)
    graph.add_edge("cancel", END)

    graph.add_conditional_edges(
        "book",
        route_after_booking,
        {
            "email": "email",
            "end": END,
        },
    )

    graph.add_edge("email", END)

    return graph.compile()


hotel_booking_graph = build_workflow()
