
from typing import Any, Literal, Optional
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage


ConversationStep = Literal[
    "collect_preferences",
    "collect_breakfast",
    "show_rooms",
    "select_room",
    "collect_guest_name",
    "collect_guest_email",
    "awaiting_confirmation",
    "booking_complete",
    "cancelled",
    "error",
]


class HotelBookingState(TypedDict, total=False):
    """Shared state for the conversational hotel booking agent."""

    # Conversation
    messages: list[BaseMessage]
    user_message: str
    response: str
    current_step: ConversationStep
    intent: Optional[str]
    use_local_parser: bool

    # Search preferences
    check_in: Optional[str]
    check_out: Optional[str]
    bed_type: Optional[str]
    breakfast: Optional[bool]
    guests: Optional[int]

    # Availability and room selection
    availability_result: Optional[dict[str, Any]]
    requested_room_no: Optional[int]

    # Guest details
    guest_name: Optional[str]
    guest_email: Optional[str]

    # Booking and email
    booking_result: Optional[dict[str, Any]]
    email_result: Optional[dict[str, Any]]

    # Confirmation and pending actions
    pending_price_question: bool
    awaiting_confirmation: bool
    booking_confirmed_by_guest: bool
