
import os
import streamlit as st
import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def get_setting(name, default=None):
    """Read Streamlit Secrets when deployed, otherwise use environment variables."""
    try:
        value = st.secrets.get(name)
        if value is not None:
            return value
    except Exception:
        # Streamlit Secrets may be unavailable in local scripts.
        pass

    return os.getenv(name, default)


def get_connection():
    """Create and return a MySQL database connection."""
    return mysql.connector.connect(
        host=get_setting("DB_HOST", "localhost"),
        port=int(get_setting("DB_PORT", 3306)),
        user=get_setting("DB_USER", "root"),
        password=get_setting("DB_PASSWORD", ""),
        database=get_setting("DB_NAME", "hotel_booking"),
        connection_timeout=15,
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
