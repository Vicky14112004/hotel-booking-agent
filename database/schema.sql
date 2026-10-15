
CREATE DATABASE IF NOT EXISTS hotel_booking;
USE hotel_booking;

CREATE TABLE IF NOT EXISTS rooms (
    room_no INT PRIMARY KEY,
    room_type VARCHAR(50) NOT NULL,
    bed_type ENUM('Single', 'Double') NOT NULL,
    breakfast BOOLEAN NOT NULL DEFAULT FALSE,
    price_per_night DECIMAL(10, 2) NOT NULL,
    floor INT NOT NULL,
    max_guests INT NOT NULL,
    status ENUM('Active', 'Maintenance') NOT NULL DEFAULT 'Active'
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id INT AUTO_INCREMENT PRIMARY KEY,
    booking_ref VARCHAR(30) NOT NULL UNIQUE,
    room_no INT NOT NULL,
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    guest_name VARCHAR(150) NOT NULL,
    guest_email VARCHAR(255) NOT NULL,
    guests INT NOT NULL,
    total_price DECIMAL(10, 2) NOT NULL,
    status ENUM('Confirmed', 'Cancelled') NOT NULL DEFAULT 'Confirmed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_booking_room_dates (room_no, check_in, check_out),
    CONSTRAINT fk_booking_room
        FOREIGN KEY (room_no) REFERENCES rooms(room_no),
    CONSTRAINT chk_booking_dates CHECK (check_out > check_in)
);
