
import pytest

from services.email_service import create_confirmation_email


def test_confirmation_email_for_confirmed_booking():
    booking = {
        "status": "confirmed",
        "booking_ref": "HTL-ABC123",
        "guest_name": "Test Guest",
        "guest_email": "test@example.com",
        "room_no": 101,
        "room_type": "Standard",
        "check_in": "2026-12-01",
        "check_out": "2026-12-03",
        "guests": 1,
        "bed_type": "Single",
        "breakfast": False,
        "nights": 2,
        "price_per_night": 2200.0,
        "total_price": 4400.0,
    }

    email = create_confirmation_email(booking)

    assert email["to"] == "test@example.com"
    assert email["status"] == "mock_sent"
    assert "HTL-ABC123" in email["subject"]
    assert "4400.00" in email["body"]


def test_email_rejected_for_unconfirmed_booking():
    with pytest.raises(ValueError):
        create_confirmation_email({"status": "booking_error"})
