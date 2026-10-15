
from datetime import datetime, timezone


def create_confirmation_email(booking):
    """Build a mock booking confirmation email."""

    if booking.get("status") != "confirmed":
        raise ValueError(
            "Cannot create a confirmation email for an unconfirmed booking."
        )

    subject = (
        f"Hotel Booking Confirmation - {booking['booking_ref']}"
    )

    body = f"""
Hello {booking['guest_name']},

Your hotel booking is confirmed!

Booking reference: {booking['booking_ref']}
Room: {booking['room_no']} ({booking['room_type']})
Check-in: {booking['check_in']}
Check-out: {booking['check_out']}
Guests: {booking['guests']}
Bed type: {booking['bed_type']}
Breakfast included: {"Yes" if booking['breakfast'] else "No"}
Nights: {booking['nights']}
Price per night: INR {booking['price_per_night']:.2f}
Total price: INR {booking['total_price']:.2f}

Thank you for choosing our hotel.
We look forward to welcoming you!
""".strip()

    email_record = {
        "to": booking["guest_email"],
        "subject": subject,
        "body": body,
        "status": "mock_sent",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Demo only: this records the email payload but does not
    # send a real email or persist the log to a database.
    print("\n--- MOCK EMAIL ---")
    print(f"To: {email_record['to']}")
    print(f"Subject: {email_record['subject']}")
    print(email_record["body"])
    print("------------------\n")

    return email_record
