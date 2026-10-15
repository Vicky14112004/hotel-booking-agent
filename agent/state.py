from typing import Any, Optional
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage


class HotelBookingState(TypedDict, total=False):
    """Shared state for the conversational hotel booking agent."""
    use_local_parser: bool
    # Conversation
    messages: list[BaseMessage]
    user_message: str
    response: str
    pending_price_question: bool

    # Guest preferences
    check_in: Optional[str]
    check_out: Optional[str]
    bed_type: Optional[str]
    breakfast: Optional[bool]
    guests: Optional[int]

    # Availability
    availability_result: Optional[dict[str, Any]]
    requested_room_no: Optional[int]

    # Guest details
    guest_name: Optional[str]
    guest_email: Optional[str]

    # Booking and email
    booking_result: Optional[dict[str, Any]]
    email_result: Optional[dict[str, Any]]

    # Workflow control
    intent: Optional[str]
    awaiting_confirmation: bool
    booking_confirmed_by_guest: bool
