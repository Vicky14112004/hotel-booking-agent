
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    """Create and return a MySQL database connection."""
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "hotel_booking"),
    )


if __name__ == "__main__":
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE(), VERSION()")
        database, version = cursor.fetchone()

        print(f"Connected to database: {database}")
        print(f"MySQL version: {version}")

        cursor.close()
    finally:
        connection.close()
