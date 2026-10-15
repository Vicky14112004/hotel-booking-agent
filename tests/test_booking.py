
from datetime import date, timedelta

from tools.booking import confirm_booking


def test_invalid_dates_do_not_create_booking():
    start = date.today() + timedelta(days=10)

    result = confirm_booking(
        room_no=101,
        check_in=start,
        check_out=start,
        guest_name="Test Guest",
        guest_email="test@example.com",
        guests=1,
        bed_type="Single",
        breakfast=False,
    )

    assert result["status"] == "invalid_dates"


def test_invalid_email_does_not_create_booking():
    start = date.today() + timedelta(days=10)

    result = confirm_booking(
        room_no=101,
        check_in=start,
        check_out=start + timedelta(days=2),
        guest_name="Test Guest",
        guest_email="invalid-email",
        guests=1,
        bed_type="Single",
        breakfast=False,
    )

    assert result["status"] == "invalid_guest"

