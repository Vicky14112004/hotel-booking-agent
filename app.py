
import base64
import html
import re
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from tools.availability import check_room_availability
from tools.booking import confirm_booking
from services.email_service import create_confirmation_email
from agent.workflow import hotel_booking_graph


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="Grand Credang | Hotel Reservations",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================================================
# SESSION STATE
# =========================================================

DEFAULTS = {
    "availability_result": None,
    "selected_room": None,
    "booking_result": None,
    "email_result": None,
    "guest_name": "",
    "guest_email": "",
    "chat_open": True,
    "chat_history": [
        {
            "role": "assistant",
            "content": (
                "Welcome to Grand Credang! ✨ I'm your Credang Concierge. "
                "I can help you find rooms, check prices, and plan your stay. "
                "What can I help you with today?"
            ),
        }
    ],
    "booking_state": {"use_local_parser": False},
    "form_check_in": date.today() + timedelta(days=1),
    "form_check_out": date.today() + timedelta(days=2),
    "form_guests": 2,
    "form_bed_type": "Double",
    "form_breakfast": True,
    "chat_input_value": "",
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# =========================================================
# CHAT HELPERS
# =========================================================

def is_room_selection_message(message):
    """Detect room selection without mistaking a new search for one."""
    text = message.strip().lower()

    if not re.search(r"\broom\s*#?\s*\d+\b", text):
        return False

    if re.fullmatch(
        r"\s*(?:i want\s+)?(?:room\s*)?#?\s*\d+\s*",
        text,
    ):
        return True

    selection_words = (
        "select",
        "selected",
        "choose",
        "chosen",
        "want",
        "take",
        "book",
        "pick",
        "go with",
    )

    return any(word in text for word in selection_words)


def is_availability_request(message):
    """Identify search requests, not room selections or confirmation replies."""
    text = message.strip().lower()

    if is_room_selection_message(text):
        return False

    phrases = (
        "availability",
        "available rooms",
        "find a room",
        "find rooms",
        "search rooms",
        "check availability",
        "check rooms",
        "show rooms",
        "show me rooms",
        "what rooms",
        "looking for a room",
        "need a room",
        "rooms available",
        "book a room",
        "book from",
        "stay from",
    )

    has_date = bool(
        re.search(
            r"\b\d{4}-\d{2}-\d{2}\b"
            r"|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
            text,
        )
    )

    return has_date or any(phrase in text for phrase in phrases)


def get_email_preview(booking_result, email_result=None):
    """
    Return an email preview for a confirmed booking.

    If the workflow hasn't populated email_result, create the mock
    email here using the existing email service.
    """
    if not isinstance(booking_result, dict):
        return None

    if booking_result.get("status") != "confirmed":
        return None

    if isinstance(email_result, dict) and email_result:
        return email_result

    try:
        return create_confirmation_email(booking_result)
    except Exception as exc:
        return {
            "status": "failed",
            "message": str(exc),
        }


def extract_booking_and_email(graph_result):
    """Read booking/email results from the graph state."""
    booking_result = graph_result.get("booking_result") or {}
    email_result = graph_result.get("email_result") or {}

    # Preserve the existing booking state for the next chat message.
    if booking_result:
        st.session_state.booking_result = booking_result

    if email_result:
        st.session_state.email_result = email_result

    preview = get_email_preview(
        booking_result,
        email_result,
    )

    if preview:
        st.session_state.email_result = preview

    return booking_result, preview


# =========================================================
# CHAT CALLBACK
# =========================================================

def send_chat_message():
    prompt = st.session_state.get("chat_input_value", "").strip()

    if not prompt:
        return

    st.session_state.chat_history.append({
        "role": "user",
        "content": prompt,
    })

    # Clear the input before Streamlit redraws.
    st.session_state.chat_input_value = ""

    try:
        current_state = dict(st.session_state.booking_state)
        current_state["user_message"] = prompt
        current_state["use_local_parser"] = False

        graph_result = hotel_booking_graph.invoke(current_state)
        print("CHAT INPUT:", prompt)
        print("GRAPH RESPONSE:", graph_result.get("response"))
        print("BREAKFAST:", graph_result.get("breakfast"))
        print("INTENT:", graph_result.get("intent"))
        print("AVAILABILITY:", graph_result.get("availability_result"))
        st.session_state.booking_state = graph_result

        reply = graph_result.get("response") or (
            "I'd be happy to help with your reservation. "
            "Please share your check-in and check-out dates, "
            "bed preference, breakfast preference, and guest count."
        )

        assistant_message = {
            "role": "assistant",
            "content": str(reply),
        }

        # -------------------------------------------------
        # FIX 1: Don't attach stale rooms to room selection.
        # -------------------------------------------------

        availability = graph_result.get("availability_result") or {}
        selection_message = is_room_selection_message(prompt)
        search_message = is_availability_request(prompt)

        response_text = str(reply).lower()
        selection_reply = bool(
            re.search(r"\byou selected room\s*#?\s*\d+", response_text)
        )

        # Attach structured room data whenever the graph actually ran a
        # fresh availability search. This includes follow-up preference
        # changes such as "I don't want breakfast", not only messages that
        # contain explicit search keywords or dates.
        if (
            graph_result.get("intent") == "availability_result"
            and not selection_message
            and not selection_reply
            and availability.get("status") in (
                "exact_match",
                "alternatives",
            )
        ):
            rooms = availability.get("rooms", [])

            if isinstance(rooms, list) and rooms:
                assistant_message["rooms"] = rooms
                assistant_message["availability_status"] = (
                    availability.get("status")
                )
                assistant_message["nights"] = availability.get(
                    "nights", 1
                )

        # -------------------------------------------------
        # FIX 2: Attach email preview to confirmed booking.
        # -------------------------------------------------

        booking_result, email_preview = extract_booking_and_email(
            graph_result
        )

        if booking_result.get("status") == "confirmed":
            assistant_message["booking_result"] = booking_result

            if email_preview:
                assistant_message["email_preview"] = email_preview

        st.session_state.chat_history.append(assistant_message)

    except Exception:
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": (
                "I'm sorry, I couldn't process that message right now. "
                "Please try again, or use the room search form."
            ),
        })


# =========================================================
# CSS
# =========================================================

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@500;600;700&display=swap');

    :root {
        --forest: #173b32;
        --forest-light: #285447;
        --gold: #c8a96b;
        --cream: #f8f5ee;
        --card: #fffdf8;
        --ink: #24352e;
        --muted: #737b73;
    }

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
    }

    .stApp {
        background: var(--cream);
        color: var(--ink);
    }

    #MainMenu, footer, header {
        visibility: hidden;
    }

    .block-container {
        max-width: 1400px;
        padding-top: 1rem;
        padding-bottom: 5rem;
    }

    h1, h2, h3 {
        font-family: 'Playfair Display', serif !important;
    }

    .top-nav {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding: 12px 4px 20px;
        border-bottom: 1px solid #ded6c6;
        margin-bottom: 22px;
    }

    .brand {
        color: var(--forest);
        font-family: 'Playfair Display', serif;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 1px;
    }

    .brand-subtitle {
        color: #88734d;
        font-size: 10px;
        letter-spacing: 3px;
        text-transform: uppercase;
    }

    .nav-note {
        color: #5e675f;
        font-size: 13px;
    }

    .hero {
        min-height: 350px;
        border-radius: 20px;
        padding: 48px 42px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        background-color: #26483c;
        background-size: cover;
        background-position: center;
        margin-bottom: 30px;
    }

    .hero-kicker {
        color: #e2c991;
        font-size: 12px;
        letter-spacing: 4px;
        text-transform: uppercase;
        margin-bottom: 12px;
    }

    .hero h1 {
        color: #fffaf0;
        font-size: clamp(36px, 5vw, 62px);
        line-height: 1.1;
        margin: 0 0 16px;
    }

    .hero p {
        max-width: 520px;
        color: #f0ece3;
        font-size: 16px;
        line-height: 1.8;
    }

    .section-kicker {
        color: #9b7a3d;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
    }

    .section-title {
        color: var(--forest);
        font-family: 'Playfair Display', serif;
        font-size: 34px;
        margin: 3px 0 8px;
    }

    .section-description {
        color: #69736b;
        margin-bottom: 22px;
    }

    .room-card {
        background: var(--card);
        border: 1px solid #e7decd;
        border-radius: 15px;
        padding: 20px;
        margin-bottom: 12px;
    }

    .room-number {
        color: #a4864d;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 2px;
        text-transform: uppercase;
    }

    .room-name {
        color: var(--forest);
        font-family: 'Playfair Display', serif;
        font-size: 23px;
        margin: 7px 0;
    }

    .room-details {
        color: #687169;
        font-size: 13px;
        line-height: 1.8;
    }

    .room-price {
        color: var(--forest);
        font-size: 25px;
        font-weight: 700;
        margin-top: 12px;
    }

    .room-price small {
        color: #7b817b;
        font-size: 12px;
        font-weight: 400;
    }

    .feature-card {
        box-sizing: border-box;
        background: var(--card);
        border: 1px solid #e7decd;
        border-radius: 14px;
        padding: 22px;
        height: 170px;
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        overflow: hidden;
    }

    .feature-icon {
        font-size: 25px;
        line-height: 1.2;
        height: 38px;
        min-height: 38px;
        margin-bottom: 8px;
    }

    .feature-title {
        color: var(--forest);
        font-size: 16px;
        font-weight: 700;
        line-height: 1.4;
        margin-bottom: 8px;
    }

    .feature-text {
        color: var(--muted);
        font-size: 13px;
        line-height: 1.6;
    }

    .confirmation-card {
        background: #edf5ee;
        border: 1px solid #b7d3bd;
        border-radius: 15px;
        padding: 22px;
        color: var(--forest);
        margin: 16px 0;
    }

    .site-footer {
        margin-top: 55px;
        padding: 28px 8px;
        text-align: center;
        border-top: 1px solid #ded6c6;
        color: var(--muted);
        font-size: 12px;
        line-height: 1.8;
    }

    /* Floating concierge button */

    .st-key-chat_launcher {
        position: fixed !important;
        right: 24px !important;
        bottom: 20px !important;
        z-index: 100001 !important;
        width: auto !important;
    }

    .st-key-chat_launcher button {
        background: var(--forest) !important;
        color: #fff4d9 !important;
        border: 1px solid var(--gold) !important;
        border-radius: 40px !important;
        min-height: 52px !important;
        padding: 0 22px !important;
        box-shadow: 0 6px 25px rgba(0,0,0,.20) !important;
        font-weight: 600 !important;
        white-space: nowrap !important;
    }

    .st-key-chat_launcher button:hover {
        background: var(--forest-light) !important;
        color: white !important;
    }

    /* Floating chatbot panel */

    .st-key-chat_widget {
        position: fixed !important;
        right: 24px !important;
        bottom: 88px !important;
        width: 420px !important;
        max-width: calc(100vw - 32px) !important;
        height: min(620px, 78vh) !important;
        box-sizing: border-box !important;
        z-index: 100000 !important;
        background: var(--card) !important;
        color: var(--ink) !important;
        border: 1px solid var(--gold) !important;
        border-radius: 22px !important;
        padding: 16px !important;
        box-shadow: 0 16px 45px rgba(23,59,50,.20) !important;
        overflow: hidden !important;
    }

    .chat-heading {
        background: var(--forest) !important;
        border-radius: 14px !important;
        padding: 16px !important;
        margin-bottom: 12px !important;
        color: #fff4d9 !important;
    }

    .chat-heading b {
        color: #fff4d9 !important;
        font-size: 18px !important;
    }

    .chat-heading small {
        color: #e2c991 !important;
        font-size: 13px !important;
    }

    .st-key-chat_history {
        height: 365px !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        padding: 2px 5px 8px 0 !important;
    }

    .st-key-chat_widget [data-testid="stChatMessage"] {
        background: #f5f1e8 !important;
        border: 1px solid #ece2d0 !important;
        border-radius: 13px !important;
        padding: 11px !important;
        margin: 7px 0 !important;
        opacity: 1 !important;
    }

    .st-key-chat_widget [data-testid="stChatMessage"] p,
    .st-key-chat_widget [data-testid="stChatMessage"] li,
    .st-key-chat_widget [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
        color: var(--ink) !important;
        opacity: 1 !important;
        line-height: 1.55 !important;
    }

    /* Chat input */

    .st-key-chat_widget .st-key-chat_input_value input {
        box-sizing: border-box !important;
        background: var(--card) !important;
        color: var(--ink) !important;
        -webkit-text-fill-color: var(--ink) !important;
        caret-color: var(--forest) !important;
        border: 1px solid #d8c8a9 !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        font-size: 14px !important;
        box-shadow: none !important;
    }

    .st-key-chat_widget .st-key-chat_input_value input::placeholder {
        color: #858b83 !important;
        -webkit-text-fill-color: #858b83 !important;
        opacity: 1 !important;
    }

    .st-key-chat_widget .st-key-chat_input_value input:focus {
        border-color: var(--forest) !important;
        box-shadow: 0 0 0 2px rgba(200,169,107,.20) !important;
    }

    .st-key-chat_widget .st-key-chat_send button {
        background: var(--forest) !important;
        color: #fff4d9 !important;
        border: 1px solid var(--gold) !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        font-size: 18px !important;
        font-weight: 600 !important;
    }

    .st-key-chat_widget .st-key-chat_send button:hover {
        background: var(--forest-light) !important;
    }

    /* Mock email preview */

    .email-preview {
        background: #fffdf8;
        border: 1px solid #c8d9cb;
        border-radius: 12px;
        padding: 14px;
        margin-top: 10px;
        color: #24352e;
        overflow-wrap: anywhere;
    }

    .email-preview-title {
        color: #173b32;
        font-size: 15px;
        font-weight: 700;
        margin-bottom: 8px;
    }

    .email-preview-meta {
        color: #687169;
        font-size: 12px;
        line-height: 1.7;
        margin-bottom: 10px;
    }

    .email-preview-body {
        color: #24352e;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        line-height: 1.65;
        font-size: 13px;
        border-top: 1px solid #e7decd;
        padding-top: 12px;
    }

    @media (max-width: 600px) {
        .top-nav {
            align-items: flex-start;
        }

        .brand {
            font-size: 23px;
        }

        .nav-note {
            display: none;
        }

        .hero {
            min-height: 320px;
            padding: 30px 24px;
        }

        .feature-card {
            height: auto;
            min-height: 160px;
            padding: 18px;
        }

        .st-key-chat_widget {
            left: 10px !important;
            right: 10px !important;
            bottom: 78px !important;
            width: auto !important;
            max-width: none !important;
            height: 76vh !important;
            padding: 12px !important;
        }

        .st-key-chat_history {
            height: 45vh !important;
        }

        .st-key-chat_launcher {
            right: 12px !important;
            bottom: 12px !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# HERO BACKGROUND
# =========================================================

image_path = Path(__file__).parent / "assets" / "hotel_background.jpg"
hero_background = ""

if image_path.exists():
    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode()

    mime_type = (
        "image/png"
        if image_path.suffix.lower() == ".png"
        else "image/jpeg"
    )

    hero_background = (
        "background-image: linear-gradient(90deg, "
        "rgba(10,27,22,.84), rgba(10,27,22,.22)), "
        f"url('data:{mime_type};base64,{encoded_image}');"
    )


# =========================================================
# HEADER AND HERO
# =========================================================

st.markdown(
    """
    <div class="top-nav">
        <div>
            <div class="brand">GRAND CREDANG</div>
            <div class="brand-subtitle">A refined stay experience</div>
        </div>
        <div class="nav-note">Comfort · Elegance · Hospitality</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="hero" style="{hero_background}">
        <div class="hero-kicker">Welcome to Grand Credang</div>
        <h1>Every Stay,<br>A Better Experience.</h1>
        <p>
            Discover a thoughtful blend of comfort, warm hospitality,
            and memorable moments. Your next stay begins here.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# ROOM SEARCH
# =========================================================

st.markdown(
    """
    <div class="section-kicker">Plan your stay</div>
    <div class="section-title">Find Your Perfect Room</div>
    <div class="section-description">
        Choose your dates and preferences to explore available rooms.
    </div>
    """,
    unsafe_allow_html=True,
)

with st.form("room_search_form"):
    col1, col2 = st.columns(2)

    with col1:
        check_in = st.date_input(
            "Check-in date",
            value=st.session_state.form_check_in,
            min_value=date.today(),
            key="search_check_in",
        )

    with col2:
        check_out = st.date_input(
            "Check-out date",
            value=st.session_state.form_check_out,
            min_value=date.today() + timedelta(days=1),
            key="search_check_out",
        )

    col3, col4, col5 = st.columns(3)

    with col3:
        guests = st.number_input(
            "Number of guests",
            min_value=1,
            max_value=10,
            value=int(st.session_state.form_guests),
            step=1,
            key="search_guests",
        )

    with col4:
        bed_type = st.selectbox(
            "Bed preference",
            ["Single", "Double"],
            index=["Single", "Double"].index(
                st.session_state.form_bed_type
            ),
            key="search_bed_type",
        )

    with col5:
        breakfast_label = st.selectbox(
            "Breakfast",
            ["Included", "Not included"],
            index=0 if st.session_state.form_breakfast else 1,
            key="search_breakfast",
        )

    submitted = st.form_submit_button(
        "Search Available Rooms",
        type="primary",
        use_container_width=True,
    )

if submitted:
    st.session_state.form_check_in = check_in
    st.session_state.form_check_out = check_out
    st.session_state.form_guests = int(guests)
    st.session_state.form_bed_type = bed_type
    st.session_state.form_breakfast = breakfast_label == "Included"

    st.session_state.selected_room = None
    st.session_state.booking_result = None
    st.session_state.email_result = None

    if check_in < date.today():
        st.error("Check-in cannot be in the past.")
    elif check_out <= check_in:
        st.error("Check-out must be after check-in.")
    else:
        try:
            with st.spinner("Checking room availability..."):
                search_result = check_room_availability(
                    check_in.isoformat(),
                    check_out.isoformat(),
                    bed_type,
                    breakfast_label == "Included",
                    int(guests),
                )

            st.session_state.availability_result = search_result

            # Keep the chatbot's booking state synchronized with the room search form.
            # This allows later chatbot price questions to reuse the same dates,
            # preferences, guest count, and verified availability results.
            booking_state = dict(st.session_state.booking_state)
            booking_state.update({
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "bed_type": bed_type,
                "breakfast": breakfast_label == "Included",
                "guests": int(guests),
                "availability_result": search_result,
                "requested_room_no": None,
                "booking_result": None,
                "email_result": None,
            })
            st.session_state.booking_state = booking_state
            st.rerun()
        except Exception:
            st.error("We couldn't check availability right now.")


# =========================================================
# ROOM RESULTS
# =========================================================

result = st.session_state.availability_result

if result:
    st.divider()

    st.markdown(
        """
        <div class="section-kicker">Your options</div>
        <div class="section-title">Rooms for Your Stay</div>
        """,
        unsafe_allow_html=True,
    )

    nights = max(
        1,
        (
            st.session_state.form_check_out
            - st.session_state.form_check_in
        ).days,
    )

    status = result.get("status")
    rooms = result.get("rooms", []) or []

    if status == "exact_match" and rooms:
        st.success(f"We found {len(rooms)} matching room(s).")

        for room in rooms:
            room_no = room.get("room_no", "N/A")
            room_type = room.get("room_type", "Room")
            room_bed = room.get("bed_type", "N/A")
            breakfast = room.get("breakfast", False)
            price = float(room.get("price_per_night", 0))
            total = float(room.get("total_price", price * nights))

            left, right = st.columns([4, 1])

            with left:
                st.markdown(
                    f"""
                    <div class="room-card">
                        <div class="room-number">
                            Room {html.escape(str(room_no))}
                        </div>
                        <div class="room-name">
                            {html.escape(str(room_type))}
                        </div>
                        <div class="room-details">
                            🛏️ {html.escape(str(room_bed))} bed
                            &nbsp; · &nbsp;
                            ☕ Breakfast {"included" if breakfast else "not included"}
                            <br>
                            👤 Up to {room.get("max_guests", st.session_state.form_guests)}
                            guest(s)
                        </div>
                        <div class="room-price">
                            ₹{price:,.0f}<small> / night</small>
                        </div>
                        <div class="room-details">
                            {nights} night(s) · Total: ₹{total:,.2f}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with right:
                st.write("")
                st.write("")
                if st.button(
                    "Select Room",
                    key=f"select_room_{room_no}",
                    use_container_width=True,
                    type="primary",
                ):
                    st.session_state.selected_room = room
                    st.session_state.guest_name = ""
                    st.session_state.guest_email = ""
                    st.rerun()

    elif status == "alternatives":
        st.warning(
            "No exact match is available for your preferences. "
            "Here are the closest alternatives."
        )

        for room in rooms[:2]:
            room_no = room.get("room_no", "N/A")
            room_type = room.get("room_type", "Room")
            price = float(room.get("price_per_night", 0))

            st.markdown(
                f"""
                <div class="room-card">
                    <div class="room-number">
                        Suggested room {html.escape(str(room_no))}
                    </div>
                    <div class="room-name">
                        {html.escape(str(room_type))}
                    </div>
                    <div class="room-details">
                        {html.escape(str(room.get("bed_type", "")))} bed
                        · Breakfast {"included" if room.get("breakfast") else "not included"}
                    </div>
                    <div class="room-price">
                        ₹{price:,.0f}<small> / night</small>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.info(
            "To book an alternative, update your preferences and search again."
        )
    else:
        st.warning(
            result.get(
                "message",
                "No suitable rooms were found. Try different dates or preferences.",
            )
        )


# =========================================================
# GUEST DETAILS AND BOOKING
# =========================================================

selected_room = st.session_state.selected_room

if selected_room:
    st.divider()

    st.markdown(
        """
        <div class="section-kicker">Almost there</div>
        <div class="section-title">Complete Your Reservation</div>
        """,
        unsafe_allow_html=True,
    )

    room_no = selected_room.get("room_no")
    room_type = selected_room.get("room_type", "Room")
    price = float(selected_room.get("price_per_night", 0))

    nights = max(
        1,
        (
            st.session_state.form_check_out
            - st.session_state.form_check_in
        ).days,
    )

    total = float(selected_room.get("total_price", price * nights))

    st.markdown(
        f"""
        <div class="room-card">
            <div class="room-name">
                Your selection: {html.escape(str(room_type))}
            </div>
            <div class="room-details">
                Room {html.escape(str(room_no))}<br>
                Check-in: {st.session_state.form_check_in.strftime("%d %b %Y")}<br>
                Check-out: {st.session_state.form_check_out.strftime("%d %b %Y")}<br>
                Guests: {st.session_state.form_guests}<br>
                Bed: {html.escape(str(selected_room.get("bed_type", "")))}<br>
                Breakfast: {"Included" if selected_room.get("breakfast") else "Not included"}
            </div>
            <div class="room-price">
                ₹{total:,.2f}<small> total stay</small>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.booking_result:
        booking = st.session_state.booking_result

        st.markdown(
            f"""
            <div class="confirmation-card">
                <h3>🎉 Booking Confirmed!</h3>
                <p>Your reservation at Grand Credang has been recorded.</p>
                <b>Booking reference:</b>
                {html.escape(str(booking.get("booking_ref", "Confirmed")))}<br>
                <b>Room:</b>
                {html.escape(str(booking.get("room_no", room_no)))}<br>
                <b>Total:</b>
                ₹{float(booking.get("total_price", total)):,.2f}
            </div>
            """,
            unsafe_allow_html=True,
        )

        email = st.session_state.email_result or {}

        if email.get("status") == "mock_sent":
            st.success("Mock confirmation email generated.")
            st.markdown("### ✉️ Confirmation email preview")

            st.markdown(
                f"""
                <div class="email-preview">
                    <div class="email-preview-title">
                        Grand Credang · Booking Confirmation
                    </div>
                    <div class="email-preview-meta">
                        <b>To:</b>
                        {html.escape(str(booking.get("guest_email", st.session_state.guest_email)))}<br>
                        <b>Subject:</b>
                        {html.escape(str(email.get("subject", "Booking Confirmation")))}
                    </div>
                    <div class="email-preview-body">{html.escape(str(email.get("body", "Email body unavailable.")))}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.caption("Preview only. No real email was sent.")

        elif email.get("status") == "failed":
            st.warning(
                "The booking was confirmed, but the mock email preview "
                "could not be generated."
            )

        if st.button("Start a New Search", type="primary"):
            st.session_state.availability_result = None
            st.session_state.selected_room = None
            st.session_state.booking_result = None
            st.session_state.email_result = None
            st.session_state.guest_name = ""
            st.session_state.guest_email = ""
            st.rerun()

    else:
        with st.form("guest_details_form"):
            guest_name = st.text_input(
                "Guest full name",
                value=st.session_state.guest_name,
                placeholder="Enter your full name",
            )

            guest_email = st.text_input(
                "Email address",
                value=st.session_state.guest_email,
                placeholder="you@example.com",
            )

            confirmed = st.checkbox(
                f"I confirm the booking details and total of ₹{total:,.2f}."
            )

            booking_submit = st.form_submit_button(
                "Confirm & Book Room",
                type="primary",
                use_container_width=True,
            )

        if booking_submit:
            st.session_state.guest_name = guest_name.strip()
            st.session_state.guest_email = guest_email.strip()

            if not guest_name.strip():
                st.error("Please enter the guest's full name.")
            elif (
                "@" not in guest_email
                or "." not in guest_email.split("@")[-1]
            ):
                st.error("Please enter a valid email address.")
            elif not confirmed:
                st.error("Please confirm the booking details.")
            else:
                try:
                    with st.spinner("Confirming your reservation..."):
                        booking_result = confirm_booking(
                            room_no=room_no,
                            check_in=st.session_state.form_check_in.isoformat(),
                            check_out=st.session_state.form_check_out.isoformat(),
                            guest_name=guest_name.strip(),
                            guest_email=guest_email.strip(),
                            guests=int(st.session_state.form_guests),
                            bed_type=selected_room.get("bed_type"),
                            breakfast=bool(selected_room.get("breakfast")),
                        )

                    if booking_result.get("status") == "confirmed":
                        st.session_state.booking_result = booking_result

                        st.session_state.email_result = get_email_preview(
                            booking_result
                        )

                        st.rerun()
                    else:
                        st.error(
                            booking_result.get(
                                "message",
                                "The booking could not be completed.",
                            )
                        )
                except Exception:
                    st.error(
                        "We couldn't complete the booking. "
                        "The room may have been booked by another guest."
                    )


# =========================================================
# AMENITIES
# =========================================================

st.divider()

st.markdown(
    """
    <div class="section-kicker">The Grand Credang experience</div>
    <div class="section-title">Thoughtful Comforts</div>
    <div class="section-description">
        Every detail is designed to make your stay comfortable.
    </div>
    """,
    unsafe_allow_html=True,
)

amenities = [
    ("🛏️", "Comfortable Rooms", "Rest easy in thoughtfully prepared rooms."),
    ("☕", "Breakfast Options", "Choose a room with or without breakfast."),
    ("🤝", "Warm Hospitality", "A welcoming experience from booking to checkout."),
    ("💬", "Digital Concierge", "Ask our assistant about availability and reservations."),
]

columns = st.columns(4)

for column, amenity in zip(columns, amenities):
    with column:
        st.markdown(
            f"""
            <div class="feature-card">
                <div class="feature-icon">{amenity[0]}</div>
                <div class="feature-title">{amenity[1]}</div>
                <div class="feature-text">{amenity[2]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================================================
# FOOTER
# =========================================================

st.markdown(
    """
    <div class="site-footer">
        <b>GRAND CREDANG</b><br>
        Every Stay, A Better Experience.<br>
        © Grand Credang · Hotel Reservations
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# FLOATING CHATBOT
# =========================================================

if st.button(
    "✕ Close Concierge" if st.session_state.chat_open
    else "💬 Chat with Concierge",
    key="chat_launcher",
):
    st.session_state.chat_open = not st.session_state.chat_open
    st.rerun()


if st.session_state.chat_open:
    with st.container(key="chat_widget"):

        st.markdown(
            """
            <div class="chat-heading">
                <b>✨ Credang Concierge</b><br>
                <small>Grand Credang · Here to help</small>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container(height=365, border=False, key="chat_history"):
            for message_index, message in enumerate(st.session_state.chat_history):
                with st.chat_message(message["role"]):
                    rooms = message.get("rooms", [])
                    status = message.get("availability_status")

                    # Avoid showing the long room list as text if cards exist.
                    if rooms and status == "exact_match":
                        st.markdown("Here are your matching rooms:")
                    elif rooms and status == "alternatives":
                        st.markdown(
                            "No exact match was available. "
                            "Here are the closest alternatives:"
                        )
                    else:
                        st.markdown(message["content"])

                    # Room cards only appear on messages with their own
                    # structured room results.
                    if rooms and status in ("exact_match", "alternatives"):
                        nights = max(1, int(message.get("nights", 1)))

                        for room in rooms:
                            room_no = room.get("room_no", "N/A")
                            room_type = room.get("room_type", "Room")
                            bed = room.get("bed_type", "N/A")
                            breakfast = room.get("breakfast", False)
                            price = float(room.get("price_per_night", 0))
                            total = float(
                                room.get("total_price", price * nights)
                            )

                            with st.container(border=True):
                                left, right = st.columns([3, 1])

                                with left:
                                    st.markdown(
                                        f"**Room {room_no} · {room_type}**"
                                    )
                                    st.caption(
                                        f"🛏️ {bed} bed · "
                                        f"☕ Breakfast "
                                        f"{'included' if breakfast else 'not included'}"
                                    )

                                with right:
                                    st.markdown(f"**₹{price:,.0f}**")
                                    st.caption("per night")

                                st.caption(
                                    f"Total for {nights} night(s): "
                                    f"₹{total:,.2f}"
                                )

                                # A real selection action for each room card.
                                if status == "exact_match" and st.button(
                                    f"Select Room {room_no}",
                                    key=f"chat_select_{message_index}_{room_no}",
                                    use_container_width=True,
                                    type="primary",
                                ):
                                    st.session_state.chat_input_value = (
                                        f"I want to book Room {room_no}"
                                    )
                                    send_chat_message()
                                    st.rerun()

                        if status == "exact_match":
                            st.caption(
                                "To continue, type your chosen room number, "
                                "for example: I want Room 203."
                            )

                    # Mock email preview inside the chatbot.
                    email = message.get("email_preview")
                    if email:
                        st.markdown("### ✉️ Confirmation email preview")

                        if email.get("status") == "mock_sent":
                            booking = message.get("booking_result") or {}

                            recipient = (
                                booking.get("guest_email")
                                or st.session_state.guest_email
                                or "Guest"
                            )

                            st.markdown(
                                f"""
                                <div class="email-preview">
                                    <div class="email-preview-title">
                                        Grand Credang · Booking Confirmation
                                    </div>
                                    <div class="email-preview-meta">
                                        <b>To:</b> {html.escape(str(recipient))}<br>
                                        <b>Subject:</b>
                                        {html.escape(str(email.get("subject", "Booking Confirmation")))}
                                    </div>
                                    <div class="email-preview-body">{html.escape(str(email.get("body", "Email body unavailable.")))}</div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                            st.caption("Preview only. No real email was sent.")

                        else:
                            st.info(
                                "The booking was confirmed, but the mock email "
                                "preview could not be generated."
                            )

        # Normal text field + Send button.
        input_col, send_col = st.columns(
            [5, 1],
            vertical_alignment="center",
            gap="small",
        )

        with input_col:
            st.text_input(
                "Chat message",
                placeholder="Ask about rooms, dates, or prices...",
                label_visibility="collapsed",
                key="chat_input_value",
                on_change=send_chat_message,
            )

        with send_col:
            st.button(
                "↑",
                key="chat_send",
                help="Send message",
                use_container_width=True,
                on_click=send_chat_message,
            )
