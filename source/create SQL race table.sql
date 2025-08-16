CREATE DATABASE race;


CREATE TABLE race (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    value TEXT NOT NULL,
    timestamp DATETIME(3) NOT NULL,
    device_id VARCHAR(32) NOT NULL,
    device_name VARCHAR(50) NOT NULL,
    race_status VARCHAR(50) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    Name VARCHAR(100) DEFAULT '',
    Vorname VARCHAR(100) DEFAULT '',
    Phone VARCHAR(50) DEFAULT '',
    `E-mail` VARCHAR(150) DEFAULT '',
    last_updated DATETIME(3) NOT NULL,
    INDEX idx_race_status (race_status),
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
