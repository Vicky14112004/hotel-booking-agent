import json

import os

import re

from datetime import date, datetime

from typing import Literal

from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI

from langchain_core.messages import HumanMessage

from langgraph.graph import StateGraph, START, END

from agent.state import HotelBookingState

from tools.availability import check_room_availability

from tools.booking import confirm_booking

from services.email_service import create_confirmation_email

load_dotenv()

# --------------------------------------------------

# GEMINI MODEL

# --------------------------------------------------

def get_model():

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:

        raise ValueError("GEMINI_API_KEY is missing from .env")

    return ChatGoogleGenerativeAI(

        model="gemini-3.5-flash-lite",

        api_key=api_key,

        max_retries=0,

        timeout=10,

    )

# --------------------------------------------------

# LOCAL MESSAGE PARSER

# --------------------------------------------------

def parse_locally(message: str, state: HotelBookingState) -> dict:

    """Extract common booking details without calling an LLM."""

    text = message.strip()

    lower = text.lower()

    updates = {}

    # Dates: YYYY-MM-DD, DD-MM-YYYY, or DD-MM-YY.

    date_matches = re.findall(

        r"\b(?:\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{2,4})\b",

        text,

    )

    parsed_dates = []

    for value in date_matches:

        try:

            if re.match(r"^\d{4}-", value):

                parsed = datetime.strptime(value, "%Y-%m-%d")

            elif len(value.rsplit("-", 1)[-1]) == 2:

                parsed = datetime.strptime(value, "%d-%m-%y")

            else:

                parsed = datetime.strptime(value, "%d-%m-%Y")

            parsed_dates.append(parsed.date().isoformat())

        except ValueError:

            continue

    # Two dates in one message replace both dates.

    if len(parsed_dates) >= 2:

        updates["check_in"] = parsed_dates[0]

        updates["check_out"] = parsed_dates[1]

    elif len(parsed_dates) == 1:

        # Respect explicit labels when a guest changes only one date.

        if "check-in" in lower or "checkin" in lower or "check in" in lower:

            updates["check_in"] = parsed_dates[0]

        elif "check-out" in lower or "checkout" in lower or "check out" in lower:

            updates["check_out"] = parsed_dates[0]

        elif not state.get("check_in"):

            updates["check_in"] = parsed_dates[0]

        else:

            updates["check_out"] = parsed_dates[0]

    # Bed type

    if re.search(r"\bsingle(?:\s+bed)?\b", lower):

        updates["bed_type"] = "Single"

    elif re.search(r"\bdouble(?:\s+bed)?\b", lower):

        updates["bed_type"] = "Double"

    # Breakfast preference. Check negative phrases first.

    negative_breakfast_phrases = (

        "don't want breakfast",

        "dont want breakfast",

        "do not want breakfast",

        "don't need breakfast",

        "dont need breakfast",

        "do not need breakfast",

        "without breakfast",

        "no breakfast",

        "breakfast not included",

        "exclude breakfast",

        "not include breakfast",

        "not included breakfast",

    )

    positive_breakfast_phrases = (

        "with breakfast",

        "breakfast yes",

        "breakfast included",

        "include breakfast",

        "want breakfast",

        "need breakfast",

    )

    if any(phrase in lower for phrase in negative_breakfast_phrases):

        updates["breakfast"] = False

    elif any(phrase in lower for phrase in positive_breakfast_phrases):

        updates["breakfast"] = True

    # In a pending price conversation, a bare yes/no is often the answer
    # to the outstanding breakfast question. Do not let Gemini interpret it
    # as a booking confirmation when no booking is awaiting confirmation.
    elif state.get("pending_price_question") and state.get("breakfast") is None:

        if lower in {"yes", "yes please", "yeah", "yep", "yup"}:

            updates["breakfast"] = True

        elif lower in {"no", "no thanks", "no please", "nope"}:

            updates["breakfast"] = False

    # Guest count, including a reply such as "3".

    guest_match = re.search(

        r"\b(\d+)\s*(?:guests?|people|persons?)\b",

        lower,

    )

    if guest_match:

        updates["guests"] = int(guest_match.group(1))

    elif lower.isdigit() and state.get("guests") is None:

        updates["guests"] = int(lower)

    # Room number selection

    room_match = re.search(

        r"\b(?:room\s*)#?(\d{3})\b",

        lower,

    )

    if room_match:

        updates["requested_room_no"] = int(room_match.group(1))

    # Email address

    email_match = re.search(

        r"\b[A-Za-z0-9.\_%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",

        text,

    )

    if email_match:

        updates["guest_email"] = email_match.group(0)

    # Name after common introductions

    name_match = re.search(

        r"\b(?:my name is|name is|guest name is)\s+"

        r"([A-Za-z][A-Za-z .'-]{0,78})",

        text,

        re.IGNORECASE,

    )

    if name_match:

        name = name_match.group(1).strip(" .,")

        if email_match:

            name = name.split(email_match.group(0))[0].strip(" ,.")

        if name:

            updates["guest_name"] = name

    elif email_match:

        # Supports a simple "Vignesh V, name\\@example.com" reply.

        before_email = text[:email_match.start()].strip(" ,:")

        if (

            before_email

            and len(before_email.split()) <= 5

            and not re.search(r"\b(room|breakfast|guests?)\b", before_email, re.I)

        ):

            updates["guest_name"] = before_email

    # Preserve earlier details; update only what was supplied.

    merged = {

        key: state.get(key)

        for key in [

            "check_in", "check_out", "bed_type", "breakfast", "guests",

            "requested_room_no", "guest_name", "guest_email",

        ]

    }

    merged.update(updates)

    affirmative = bool(

        re.fullmatch(

            r"\s*(yes|yeah|yep)\s*[,!. ]*"

            r"(confirm|confirm booking|confirm my booking|"

            r"proceed|go ahead)?\s*[,!. ]*",

            lower,

        )

    )

    negative = lower in {

        "no", "cancel", "no thanks", "don't book", "do not book",

    }

    if negative and state.get("awaiting_confirmation"):

        updates["intent"] = "cancel_confirmation"

        updates["response"] = (

            "No booking has been made. You can change your preferences "

            "or search for another room."

        )

    elif affirmative and state.get("awaiting_confirmation"):

        updates["intent"] = "confirm_booking"

        updates["booking_confirmed_by_guest"] = True

    elif lower in {"no", "cancel", "no thanks", "don't book", "do not book"}:

        updates["intent"] = "cancel_confirmation"

        updates["booking_confirmed_by_guest"] = False

        updates["awaiting_confirmation"] = False

        updates["response"] = "No booking has been made."

    elif room_match:

        updates["intent"] = "select_room"

    elif (

        email_match

        or name_match

        or (

            state.get("intent") == "collect_guest_details"

            and len(text.split()) >= 1

            and not updates.get("check_in")

            and not updates.get("check_out")

            and not updates.get("bed_type")

            and "breakfast" not in lower

            and not guest_match

        )

    ):

        updates["intent"] = "provide_details"

        # If this is a name-only answer, don't overwrite a previous name

        # unless it looks like a reasonable name.

        if (

            not email_match

            and not name_match

            and not state.get("guest_name")

            and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,78}", text)

        ):

            updates["guest_name"] = text.strip()

    elif any(

        phrase in lower

        for phrase in (

            "price", "how much", "cost", "total for", "per night",

            "what will it cost", "rate for",

        )

    ):

        updates["intent"] = "price"

    elif (

        any(updates.get(key) is not None for key in [

            "check_in", "check_out", "bed_type", "breakfast", "guests"

        ])

        or any(

            word in lower

            for word in ["search", "available", "availability", "change dates"]

        )

    ):

        updates["intent"] = "search"

    else:

        updates["intent"] = "other"

        updates["response"] = (

            "Welcome! I can help you book a room. Please tell me your "

            "check-in and check-out dates, bed type, breakfast preference, "

            "and number of guests."

        )

    # Keep a pending price request active while the guest provides details.
    if updates.get("intent") == "price":
        updates["pending_price_question"] = True

    updates["messages"] = state.get("messages", []) + [

        HumanMessage(content=text)

    ]

    return updates

# --------------------------------------------------

# INTERPRET USER MESSAGE — GEMINI FIRST

# --------------------------------------------------

def interpret_message(state: HotelBookingState) -> dict:

    message = state.get("user_message", "").strip()

    if not message:

        return {

            "intent": "other",

            "response": (

                "Welcome! Please tell me your preferred dates, "

                "bed type, breakfast preference, and number of guests."

            ),

        }

    # Handle simple yes/no confirmation deterministically before Gemini.

    # This prevents the model from misclassifying "yes" as "booking".

    # Normalize punctuation so phrases like "yes, confirm", "Yes Confirm!",

    # and "yes confirm booking" are handled consistently.

    lower_message = re.sub(r"[^a-z0-9' ]+", " ", message.lower())

    lower_message = re.sub(r"\s+", " ", lower_message).strip()

    affirmative_messages = {

        "yes", "yes please", "yeah", "yep", "yup",

        "confirm", "confirm booking", "yes confirm",

        "yes confirm booking", "yes please confirm",

        "yes please confirm booking", "confirm my booking",

        "i confirm", "i confirm booking", "proceed",

        "go ahead", "book it", "please book it",

    }

    negative_messages = {

        "no", "no thanks", "cancel", "dont book",

        "do not book", "no cancel", "no please cancel",

        "no i dont want to book", "no i don't want to book",

        "no i do not want to book", "i dont want to book",

        "i don't want to book", "i do not want to book",

        "i do not wish to book", "dont want to book",

        "don't want to book", "do not want to book",

        "no i dont want to confirm", "no i don't want to confirm",

        "no i do not want to confirm", "dont confirm", "don't confirm",

    }

    # Accept natural-language declines and punctuation variations, but only

    # when the guest is explicitly declining the pending reservation.

    is_negative = (

        lower_message in negative_messages

        or bool(re.fullmatch(

            r"(?:no[, ]+)?(?:i )?(?:do not|don't|dont) "

            r"(?:want|wish) to (?:book|confirm)(?: the booking)?",

            lower_message,

        ))

        or bool(re.fullmatch(

            r"no[, ]+(?:i )?(?:do not|don't|dont) "

            r"(?:want|wish) to (?:book|confirm)(?: the booking)?",

            lower_message,

        ))

    )

    # Accept a short affirmative confirmation even if the wording varies.

    is_affirmative = (

        lower_message in affirmative_messages

        or bool(re.fullmatch(

            r"(yes|yeah|yep|yup)( please)? (confirm|book|proceed)( booking| it)?",

            lower_message,

        ))

    )

    # If the last response quoted the cheapest room and invited the guest to
    # choose it, a plain "yes" means select that quoted room, not start a
    # booking without a room number.
    previous_result = state.get("availability_result") or {}
    previous_rooms = previous_result.get("rooms") or []
    previous_response = str(state.get("response") or "").lower()
    previous_intent = state.get("intent")
    accepted_price_offer = (
        is_affirmative
        and not state.get("awaiting_confirmation")
        and previous_intent == "price"
        and state.get("pending_price_question") is False
        and bool(previous_rooms)
        and (
            "cheapest available option" in previous_response
            or "would you like to choose this room" in previous_response
        )
    )
    if accepted_price_offer:
        nights = int(previous_result.get("nights") or 1)
        cheapest_room = min(
            previous_rooms,
            key=lambda room: float(
                room.get("total_price")
                if room.get("total_price") is not None
                else float(room["price_per_night"]) * nights
            ),
        )
        print(
            f"[PRICE] Guest accepted cheapest room offer: "
            f"Room {cheapest_room.get('room_no')}"
        )
        return {
            "intent": "provide_details",
            "requested_room_no": cheapest_room["room_no"],
            "pending_price_question": False,
            "booking_confirmed_by_guest": False,
            "awaiting_confirmation": False,
            "messages": state.get("messages", []) + [
                HumanMessage(content=message)
            ],
            "response": (
                f"Great, you've selected Room {cheapest_room['room_no']} "
                f"({cheapest_room['room_type']}). Please provide the guest's full name."
            ),
        }

    if state.get("awaiting_confirmation") and is_affirmative:

        print("[CONFIRMATION] Guest explicitly confirmed the booking.")

        return {

            "intent": "confirm_booking",

            "booking_confirmed_by_guest": True,

            "awaiting_confirmation": True,

            "messages": state.get("messages", []) + [HumanMessage(content=message)],

        }

    if state.get("awaiting_confirmation") and is_negative:

        print("[CONFIRMATION] Guest declined the booking.")

        return {

            "intent": "cancel_confirmation",

            "booking_confirmed_by_guest": False,

            "awaiting_confirmation": False,

            "response": (

                "No booking has been made. You can change your preferences "

                "or search for another room whenever you're ready."

            ),

            "messages": state.get("messages", []) + [HumanMessage(content=message)],

        }

    print("\n========== INTERPRET MESSAGE ==========")

    print("USER MESSAGE:", message)

    # Use the local parser only when explicitly requested.

    if state.get("use_local_parser") is True:

        print("[PARSER] Local parser explicitly enabled.")

        return parse_locally(message, state)

    existing_info = {

        "check_in": state.get("check_in"),

        "check_out": state.get("check_out"),

        "bed_type": state.get("bed_type"),

        "breakfast": state.get("breakfast"),

        "guests": state.get("guests"),

        "requested_room_no": state.get("requested_room_no"),

        "guest_name": state.get("guest_name"),

        "guest_email": state.get("guest_email"),

        "intent": state.get("intent"),

        "availability_result": state.get("availability_result"),

    }

    prompt = f"""

You are the conversational intelligence for Grand Credang Hotel.

Extract the guest's latest message into a single valid JSON object.

Do not return Markdown, code fences, or explanations.

Fields:

- check_in: YYYY-MM-DD string or null

- check_out: YYYY-MM-DD string or null

- bed_type: "Single", "Double", or null

- breakfast: true, false, or null

- guests: positive integer or null

- requested_room_no: room number as a string or null

- guest_name: string or null

- guest_email: string or null

- intent: one of

  "availability", "search", "select_room", "booking",

  "price", "change_preferences", "other"

Important rules:

1. Interpret the latest message in the context of the existing booking.

2. Preserve existing information when the guest does not change it.

3. If the guest says they do not want breakfast, return breakfast=false.

4. If the guest says they want breakfast, return breakfast=true.

5. A preference change must use intent="change_preferences".

6. If the guest asks to find available rooms, use intent="availability".

7. If the guest selects a specific room, use intent="select_room".

8. If the guest asks about prices, use intent="price".

9. Never invent dates, room numbers, names, or email addresses.

10. Use null for fields that cannot be extracted from the latest message

    and are not already known from the existing information.

Existing booking information:

{json.dumps(existing_info, default=str)}

Latest guest message:

{message}

Return only the JSON object.

"""

    try:

        print("[GEMINI] Calling Gemini API...")

        response = get_model().invoke(prompt)

        print("[GEMINI] API call succeeded.")

        print("[GEMINI] RAW RESPONSE:", response.content)

        # Gemini can return either a string or a list of content blocks.

        content = response.content

        if isinstance(content, list):

            text_parts = []

            for block in content:

                if isinstance(block, dict):

                    if block.get("type") == "text":

                        text_parts.append(block.get("text", ""))

                elif isinstance(block, str):

                    text_parts.append(block)

            content = "\n".join(text_parts)

        if not isinstance(content, str):

            content = str(content)

        content = content.strip()

        # Remove Markdown fences if the model returns them.

        if content.startswith("```"):

            lines = content.splitlines()

            if lines and lines[0].startswith("```"):

                lines = lines[1:]

            if lines and lines[-1].strip() == "```":

                lines = lines[:-1]

            content = "\n".join(lines).strip()

        # Extract JSON if the response contains surrounding text.

        start = content.find("{")

        end = content.rfind("}")

        if start == -1 or end == -1 or end < start:

            raise ValueError(

                f"Gemini did not return a valid JSON object: {content}"

            )

        extracted = json.loads(content[start:end + 1])

        print("[GEMINI] PARSED JSON:", extracted)

        # Preserve existing booking details unless the guest

        # explicitly provides a replacement value.

        updates = {}

        allowed_fields = [

            "check_in",

            "check_out",

            "bed_type",

            "breakfast",

            "guests",

            "requested_room_no",

            "guest_name",

            "guest_email",

        ]

        for key in allowed_fields:

            value = extracted.get(key)

            # Important: False is a valid value for breakfast.

            if value is not None:

                updates[key] = value

        # Deterministically extract dates/preferences too. Gemini sometimes returns null
        # for compact inputs such as "checkin 27-11-26 checkout 29-11-26".
        # The local parser acts as a safety net; explicit Gemini values take priority.
        local_updates = parse_locally(message, state)
        for key in allowed_fields:
            if key not in updates and local_updates.get(key) is not None:
                updates[key] = local_updates[key]

        valid_intents = {

            "availability",

            "search",

            "select_room",

            "booking",

            "price",

            "change_preferences",

            "other",

        }

        intent = extracted.get("intent", "other")

        if intent not in valid_intents:

            intent = "other"

        # Preserve a price request while the guest supplies missing details
        # across multiple messages. LangGraph only persists keys declared in
        # HotelBookingState, so pending_price_question is also declared there.
        pending_price = bool(state.get("pending_price_question")) or intent == "price"

        # While a price question is waiting for required details, all ordinary
        # preference replies belong to that request. Gemini may incorrectly
        # label "yes" as booking or a bed/breakfast change as availability.
        # Keep routing to the price node until it has enough data to search.
        # A room selection is allowed only after the price answer has been
        # delivered and pending_price_question has been cleared.
        if pending_price and not state.get("awaiting_confirmation"):
            intent = "price"

        updates["intent"] = intent
        if pending_price:
            updates["pending_price_question"] = True

        updates["use_local_parser"] = False

        # Keep conversation history.

        updates["messages"] = (

            state.get("messages", [])

            + [HumanMessage(content=message)]

        )

        # Print the fields that will be passed to the graph.

        print("[GEMINI] INTENT:", updates.get("intent"))

        print("[GEMINI] BREAKFAST:", updates.get("breakfast"))

        print("[GEMINI] CHECK-IN:", updates.get("check_in"))

        print("[GEMINI] CHECK-OUT:", updates.get("check_out"))

        print("[GEMINI] GUESTS:", updates.get("guests"))

        print("[GEMINI] ROOM:", updates.get("requested_room_no"))

        print("[GEMINI] Returning extracted information.")

        print("=======================================\n")

        return updates

    except Exception as exc:

        print(

            f"[GEMINI ERROR] {type(exc).__name__}: {exc}"

        )

        print("[PARSER] Falling back to local parser for this message.")

        # Fall back only for this message. Do not permanently disable

        # Gemini for subsequent messages.

        updates = parse_locally(message, state)

        updates["use_local_parser"] = False

        return updates

# --------------------------------------------------

# AVAILABILITY

# --------------------------------------------------

def validate_and_search(state: HotelBookingState) -> dict:

    required = [

        "check_in", "check_out", "bed_type", "breakfast", "guests"

    ]

    missing = [

        key for key in required

        if state.get(key) is None

    ]

    if missing:

        prompts = {

            "check_in": "What is your check-in date? (DD-MM-YYYY)",

            "check_out": "What is your check-out date? (DD-MM-YYYY)",

            "bed_type": "Would you like a Single or Double bed?",

            "breakfast": "Would you like breakfast included? (Yes or No)",

            "guests": "How many guests will be staying?",

        }

        return {

            "intent": "collect_preferences",

            "response": " ".join(prompts[key] for key in missing),

        }

    try:

        check_in = date.fromisoformat(state["check_in"])

        check_out = date.fromisoformat(state["check_out"])

    except (TypeError, ValueError):

        return {

            "intent": "invalid_dates",

            "response": "Please enter valid dates such as 17-12-2026.",

        }

    if check_in < date.today() or check_out <= check_in:

        return {

            "intent": "invalid_dates",

            "response": (

                "Check-in must be today or later, and check-out "

                "must be after check-in. Please provide new dates."

            ),

        }

    result = check_room_availability(

        check_in=state["check_in"],

        check_out=state["check_out"],

        bed_type=state["bed_type"],

        breakfast=state["breakfast"],

        guests=state["guests"],

    )

    result["request"] = {

        "check_in": state["check_in"],

        "check_out": state["check_out"],

        "bed_type": state["bed_type"],

        "breakfast": state["breakfast"],

        "guests": state["guests"],

    }

    status = result.get("status")

    if status == "exact_match":

        rooms = result.get("rooms", [])

        lines = [

            f"Room {room['room_no']} — {room['room_type']}, "

            f"{room['bed_type']} bed, "

            f"breakfast {'included' if room['breakfast'] else 'not included'}, "

            f"₹{float(room['price_per_night']):,.2f}/night, "

            f"total ₹{float(room['total_price']):,.2f}."

            for room in rooms

        ]

        response = (

            "Here are the matching rooms:\n"

            + "\n".join(lines)

            + "\nWhich room would you like to book?"

            if lines else "No exact-match rooms were returned."

        )

    elif status == "alternatives":

        rooms = result.get("rooms", [])[:2]

        lines = [

            f"Room {room['room_no']} — {room['room_type']}, "

            f"{room['bed_type']} bed, "

            f"breakfast {'included' if room['breakfast'] else 'not included'}, "

            f"₹{float(room['price_per_night']):,.2f}/night."

            for room in rooms

        ]

        response = (

            "No exact match is available. Here are alternatives:\n"

            + "\n".join(lines)

            if lines else "No suitable alternatives are available."

        )

    elif status == "invalid_dates":

        response = "The selected dates are invalid. Please try again."

    elif status == "invalid_preferences":

        response = "Please check the room preferences and guest count."

    else:

        response = result.get(

            "message",

            "No suitable rooms are available for those dates and guest count.",

        )

    # Complete a pending price request once all preferences are known. Do not
    # show the full room list when the guest was trying to find the cheapest room.
    if state.get("pending_price_question") and status in {"exact_match", "alternatives"}:
        available_rooms = result.get("rooms", [])
        if available_rooms:
            nights = int(result.get("nights") or 1)
            cheapest = min(
                available_rooms,
                key=lambda room: float(
                    room.get("total_price")
                    if room.get("total_price") is not None
                    else float(room["price_per_night"]) * nights
                ),
            )
            total = float(
                cheapest.get("total_price")
                if cheapest.get("total_price") is not None
                else float(cheapest["price_per_night"]) * nights
            )
            return {
                "availability_result": result,
                "requested_room_no": None,
                "booking_result": None,
                "email_result": None,
                "awaiting_confirmation": False,
                "booking_confirmed_by_guest": False,
                "pending_price_question": False,
                "intent": "price",
                "response": (
                    f"The cheapest available option matching your preferences is "
                    f"Room {cheapest['room_no']} ({cheapest['room_type']}), at "
                    f"₹{float(cheapest['price_per_night']):,.2f} per night. "
                    f"For {nights} night(s), the total is ₹{total:,.2f}. "
                    f"Breakfast is {'included' if cheapest['breakfast'] else 'not included'}. "
                    "Would you like to choose this room or compare other options?"
                ),
            }

    # A new search invalidates any previous room selection and confirmation.

    return {

        "availability_result": result,

        "requested_room_no": None,

        "booking_result": None,

        "email_result": None,

        "awaiting_confirmation": False,

        "booking_confirmed_by_guest": False,

        "intent": "availability_result",

        "response": response,

    }

def answer_price_question(state: HotelBookingState) -> dict:

    """Answer price questions using current or previously verified room rates."""

    result = state.get("availability_result") or {}

    rooms = result.get("rooms") or []

    status = result.get("status")

    room_no = state.get("requested_room_no")

    required = ("check_in", "check_out", "bed_type", "breakfast", "guests")

    expected_request = {

        key: state.get(key)

        for key in required

    }

    stored_request = result.get("request") or {}

    stale_results = stored_request != expected_request

    # Refresh availability only if stored results cannot be trusted.

    if stale_results or status not in {"exact_match", "alternatives"} or not rooms:

        if not all(state.get(key) is not None for key in required):
            next_question = {
                "check_in": "What is your check-in date? (DD-MM-YYYY)",
                "check_out": "What is your check-out date? (DD-MM-YYYY)",
                "bed_type": "Would you prefer a Single or Double bed?",
                "breakfast": "Would you like breakfast included? (yes or no)",
                "guests": "How many guests will be staying?",
            }
            missing = [key for key in required if state.get(key) is None]
            return {
                "intent": "price",
                "pending_price_question": True,
                "response": (
                    "To calculate the cheapest available room, I need a few details. "
                    + next_question[missing[0]]
                ),
            }

        refreshed = validate_and_search(state)

        result = refreshed.get("availability_result") or {}

        rooms = result.get("rooms") or []

        status = result.get("status")

        print("\n========== PRICE DEBUG ==========")

        print("USER MESSAGE:", state.get("user_message"))

        print("CHECK-IN:", state.get("check_in"))

        print("CHECK-OUT:", state.get("check_out"))

        print("BED TYPE:", state.get("bed_type"))

        print("BREAKFAST:", state.get("breakfast"))

        print("GUESTS:", state.get("guests"))

        print("AVAILABILITY STATUS:", status)

        print("ROOM COUNT:", len(rooms))

        print("STORED REQUEST:", result.get("request"))

        print("================================\n")

        if status not in {"exact_match", "alternatives"} or not rooms:

            return {

                **refreshed,

                "intent": "price",

                "pending_price_question": True,

                "response": (

                    "I couldn't find an available room matching those "

                    "preferences for your dates. Would you like to change "

                    "your preferences or try different dates?"

                ),

            }

    nights = int(result.get("nights") or 1)

    # A general cheapest-price question should always return the cheapest

    # room, rather than automatically choosing a previously selected room.

    cheapest = min(

        rooms,

        key=lambda room: float(

            room.get("total_price")

            if room.get("total_price") is not None

            else float(room["price_per_night"]) * nights

        ),

    )

    # If the guest explicitly asks about a particular room, answer for it.

    message = (state.get("user_message") or "").lower()

    asks_cheapest = any(

        word in message

        for word in ("cheapest", "lowest price", "least expensive")

    )

    selected = next(

        (

            room for room in rooms

            if str(room.get("room_no")) == str(room_no)

        ),

        None,

    )

    room = cheapest if asks_cheapest or selected is None else selected

    total = float(

        room.get("total_price")

        if room.get("total_price") is not None

        else float(room["price_per_night"]) * nights

    )

    return {

        "availability_result": result,

        "intent": "price",

        "pending_price_question": False,

        "response": (

            f"{'The cheapest available option is' if asks_cheapest or selected is None else 'Your selected room is'} "

            f"Room {room['room_no']} ({room['room_type']}), at "

            f"₹{float(room['price_per_night']):,.2f} per night. "

            f"For {nights} night(s), the total is ₹{total:,.2f}. "

            f"Breakfast is {'included' if room['breakfast'] else 'not included'}. "

            "Would you like to choose this room or compare the other options?"

        ),

    }

# --------------------------------------------------

# ROOM SELECTION AND GUEST DETAILS

# --------------------------------------------------

def handle_room_selection(state: HotelBookingState) -> dict:

    """Validate a selected exact match or alternative against fresh availability."""

    result = state.get("availability_result") or {}

    room_no = state.get("requested_room_no")

    status = result.get("status")

    if status not in {"exact_match", "alternatives"}:

        return {

            "intent": "selection_failed",

            "response": "Please search for available rooms first.",

        }

    selected = next(

        (room for room in result.get("rooms", [])

         if str(room["room_no"]) == str(room_no)),

        None,

    )

    if not selected:

        return {

            "intent": "selection_failed",

            "response": (

                "That room is not in the latest available options. "

                "Please choose one of the rooms shown."

            ),

        }

    if status == "alternatives":

        fresh_result = check_room_availability(

            check_in=state["check_in"],

            check_out=state["check_out"],

            bed_type=selected["bed_type"],

            breakfast=bool(selected["breakfast"]),

            guests=state["guests"],

        )

        fresh_result["request"] = {

            "check_in": state["check_in"],

            "check_out": state["check_out"],

            "bed_type": selected["bed_type"],

            "breakfast": bool(selected["breakfast"]),

            "guests": state["guests"],

        }

        if fresh_result.get("status") != "exact_match":

            return {

                "availability_result": fresh_result,

                "requested_room_no": None,

                "intent": "availability_result",

                "response": (

                    "That alternative is no longer available for your stay. "

                    "I've refreshed availability; please choose another option."

                ),

            }

        selected_fresh = next(

            (room for room in fresh_result.get("rooms", [])

             if str(room["room_no"]) == str(room_no)),

            None,

        )

        if not selected_fresh:

            return {

                "availability_result": fresh_result,

                "requested_room_no": None,

                "intent": "availability_result",

                "response": (

                    "That alternative is no longer available for your stay. "

                    "Please choose a room from these refreshed results."

                ),

            }

        selected = selected_fresh

        return {

            "availability_result": fresh_result,

            "requested_room_no": selected["room_no"],

            "bed_type": selected["bed_type"],

            "breakfast": bool(selected["breakfast"]),

            "intent": "collect_guest_details",

            "response": (

                f"Room {selected['room_no']} ({selected['room_type']}) is available "

                f"with a {selected['bed_type']} bed and breakfast "

                f"{'included' if selected['breakfast'] else 'not included'}, "

                f"at ₹{float(selected['price_per_night']):,.2f} per night "

                f"(₹{float(selected['total_price']):,.2f} total for "

                f"{fresh_result.get('nights', 1)} night(s)). "

                "Please provide the guest's full name and email address."

            ),

        }

    return {

        "requested_room_no": selected["room_no"],

        "intent": "collect_guest_details",

        "response": (

            f"You selected Room {selected['room_no']} ({selected['room_type']}) at "

            f"₹{float(selected['price_per_night']):,.2f} per night "

            f"(₹{float(selected['total_price']):,.2f} total). "

            "Please provide the guest's full name and email address."

        ),

    }

def collect_details(state: HotelBookingState) -> dict:

    """Collect and validate guest details, then request explicit confirmation."""

    if not state.get("guest_name"):

        return {

            "intent": "collect_guest_details",

            "response": "Please provide the guest's full name.",

        }

    if not state.get("guest_email"):

        return {

            "intent": "collect_guest_details",

            "response": "Please provide the guest's email address.",

        }

    guest_name = str(state.get("guest_name", "")).strip()

    guest_email = str(state.get("guest_email", "")).strip()

    if not guest_name or len(guest_name) > 100:

        return {

            "intent": "collect_guest_details",

            "response": "Please provide a valid full name (100 characters or fewer).",

        }

    if (

        len(guest_email) > 255

        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", guest_email)

    ):

        return {

            "intent": "collect_guest_details",

            "response": "That email address doesn't look valid. Please enter a valid email address.",

        }

    result = state.get("availability_result") or {}

    room_no = state.get("requested_room_no")

    selected = next(

        (

            room for room in result.get("rooms", [])

            if str(room["room_no"]) == str(room_no)

        ),

        None,

    )

    if result.get("status") != "exact_match" or not selected:

        return {

            "intent": "selection_failed",

            "response": "Please select a currently available room again.",

        }

    nights = int(result.get("nights") or 1)

    total = float(selected.get("total_price", selected["price_per_night"] * nights))

    return {

        "intent": "awaiting_confirmation",

        "awaiting_confirmation": True,

        "booking_confirmed_by_guest": False,

        "response": (

            "Please review your booking:\n"

            f"Room {selected['room_no']} — {selected['room_type']}\n"

            f"Bed: {selected['bed_type']}\n"

            f"Check-in: {state['check_in']}\n"

            f"Check-out: {state['check_out']}\n"

            f"Guests: {state['guests']}\n"

            f"Breakfast: {'included' if selected['breakfast'] else 'not included'}\n"

            f"Price per night: ₹{float(selected['price_per_night']):,.2f}\n"

            f"Number of nights: {nights}\n"

            f"Total: ₹{total:,.2f}\n\n"

            "Reply 'yes, confirm booking' to proceed, or 'no' to cancel."

        ),

    }

# --------------------------------------------------

# BOOKING AND MOCK EMAIL

# --------------------------------------------------

def book_room(state: HotelBookingState) -> dict:

    # Prevent accidental duplicate bookings.

    existing_booking = state.get("booking_result") or {}

    if existing_booking.get("status") == "confirmed":

        return {

            "intent": "booking_confirmed",

            "awaiting_confirmation": False,

            "booking_confirmed_by_guest": False,

            "response": (

                f"Your booking is already confirmed. Reference: "

                f"{existing_booking.get('booking_ref', 'N/A')}."

            ),

        }

    if not state.get("booking_confirmed_by_guest"):

        return {

            "intent": "awaiting_confirmation",

            "response": "Please explicitly confirm before I make the booking.",

        }

    result = state.get("availability_result") or {}

    rooms = result.get("rooms", [])

    room_no = state.get("requested_room_no")

    selected = next(

        (room for room in rooms if str(room["room_no"]) == str(room_no)),

        None,

    )

    if not selected:

        return {

            "intent": "booking_failed",

            "response": "Please search availability and select a room again.",

        }

    booking = confirm_booking(

        room_no=selected["room_no"],

        check_in=state["check_in"],

        check_out=state["check_out"],

        guest_name=state["guest_name"],

        guest_email=state["guest_email"],

        guests=state["guests"],

        bed_type=state["bed_type"],

        breakfast=state["breakfast"],

    )

    if booking.get("status") != "confirmed":

        return {

            "booking_result": booking,

            "intent": "booking_failed",

            "response": (

                "The booking could not be confirmed. The room may have "

                "been booked by someone else. Please search again."

            ),

        }

    return {

        "booking_result": booking,

        "intent": "booking_confirmed",

        "awaiting_confirmation": False,

        "booking_confirmed_by_guest": False,

        "response": (

            f"Your booking is confirmed! Reference: "

            f"{booking.get('booking_ref', 'N/A')}."

        ),

    }

def send_confirmation_email(state: HotelBookingState) -> dict:

    booking = state.get("booking_result") or {}

    if booking.get("status") != "confirmed":

        return {

            "intent": "booking_failed",

            "response": "No confirmation email was generated.",

        }

    email_result = create_confirmation_email(booking)

    return {

        "email_result": email_result,

        "response": state.get("response", "")

        + "\nA mock confirmation email was generated. No real email was sent.",

    }

# --------------------------------------------------

# GRAPH ROUTING

# --------------------------------------------------

def route_after_interpretation(

    state: HotelBookingState,

) -> Literal["search", "select", "details", "book", "price", "end"]:

    intent = state.get("intent")

    if intent in {"search", "availability", "change_preferences"}:

        return "search"

    if intent == "select_room":

        return "select"

    if intent in {"provide_details", "booking"}:

        return "details"

    if intent == "confirm_booking":

        return "book"

    if intent == "price":

        return "price"

    return "end"

def build_workflow():

    graph = StateGraph(HotelBookingState)

    graph.add_node("interpret", interpret_message)

    graph.add_node("search", validate_and_search)

    graph.add_node("select", handle_room_selection)

    graph.add_node("details", collect_details)

    graph.add_node("price", answer_price_question)

    graph.add_node("book", book_room)

    graph.add_node("email", send_confirmation_email)

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

            "end": END,

        },

    )

    graph.add_edge("search", END)

    graph.add_edge("select", END)

    graph.add_edge("details", END)

    graph.add_edge("price", END)

    graph.add_conditional_edges(

        "book",

        lambda state: (

            "email"

            if state.get("intent") == "booking_confirmed"

            else "end"

        ),

        {"email": "email", "end": END},

    )

    graph.add_edge("email", END)

    return graph.compile()

hotel_booking_graph = build_workflow()