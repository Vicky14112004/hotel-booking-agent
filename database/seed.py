
from database.db import get_connection


ROOMS = [
    # room_no, room_type, bed_type, breakfast, price, floor, max_guests, status
    (101, "Standard", "Single", False, 2200, 1, 1, "Active"),
    (102, "Standard", "Single", True, 2600, 1, 1, "Active"),
    (103, "Standard", "Double", False, 3000, 1, 2, "Active"),
    (104, "Standard", "Double", True, 3400, 1, 2, "Active"),
    (105, "Standard", "Single", False, 2200, 1, 1, "Active"),
    (106, "Standard", "Double", True, 3400, 1, 2, "Active"),
    (107, "Standard", "Double", False, 3000, 1, 2, "Active"),
    (108, "Standard", "Single", True, 2600, 1, 1, "Active"),

    (201, "Deluxe", "Double", True, 4500, 2, 2, "Active"),
    (202, "Deluxe", "Double", False, 4100, 2, 2, "Active"),
    (203, "Deluxe", "Single", True, 3800, 2, 1, "Active"),
    (204, "Deluxe", "Double", True, 4500, 2, 2, "Active"),
    (205, "Deluxe", "Single", False, 3500, 2, 1, "Active"),
    (206, "Deluxe", "Double", False, 4100, 2, 2, "Active"),
    (207, "Deluxe", "Double", True, 4500, 2, 2, "Active"),
    (208, "Deluxe", "Single", True, 3800, 2, 1, "Active"),

    (301, "Suite", "Double", True, 6500, 3, 2, "Active"),
    (302, "Suite", "Double", False, 6000, 3, 2, "Active"),
    (303, "Suite", "Double", True, 6500, 3, 2, "Active"),
    (304, "Suite", "Double", True, 6500, 3, 2, "Active"),
    (305, "Suite", "Double", False, 6000, 3, 2, "Active"),
    (306, "Suite", "Double", True, 6500, 3, 2, "Active"),

    (401, "Deluxe", "Double", True, 4500, 4, 2, "Maintenance"),
    (402, "Standard", "Single", False, 2200, 4, 1, "Active"),
]


def seed_rooms():
    connection = get_connection()

    try:
        cursor = connection.cursor()

        query = """
            INSERT INTO rooms (
                room_no, room_type, bed_type, breakfast,
                price_per_night, floor, max_guests, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                room_type = VALUES(room_type),
                bed_type = VALUES(bed_type),
                breakfast = VALUES(breakfast),
                price_per_night = VALUES(price_per_night),
                floor = VALUES(floor),
                max_guests = VALUES(max_guests),
                status = VALUES(status)
        """

        cursor.executemany(query, ROOMS)
        connection.commit()

        cursor.execute("SELECT COUNT(*) FROM rooms")
        count = cursor.fetchone()[0]

        print(f"Room seeding completed. Total rooms: {count}")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    seed_rooms()
