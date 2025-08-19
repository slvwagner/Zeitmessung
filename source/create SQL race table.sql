CREATE DATABASE IF NOT EXISTS zeitmessung_V2;
USE zeitmessung_V2;

CREATE TABLE participant (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    race_order INT DEFAULT NULL,
    last_run INT DEFAULT NULL,
    next_run INT DEFAULT 1,
    Name VARCHAR(100) DEFAULT '',
    Vorname VARCHAR(100) DEFAULT '',
    Phone VARCHAR(50) DEFAULT '',
    `E-mail` VARCHAR(50) DEFAULT '',
    Kategorie VARCHAR(255) DEFAULT '',
    Gewicht DOUBLE NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE race (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    participant_id INT UNSIGNED,
    run INT UNSIGNED DEFAULT 1,
    Start_timestamp DATETIME(3) NULL,
    Interim_time DATETIME(3) NULL,
    End_timestamp DATETIME(3) NULL,
    device_id VARCHAR(32) NOT NULL,
    device_name VARCHAR(50) NOT NULL,
    race_status VARCHAR(50) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_race_status (race_status),
    INDEX idx_participant_id (participant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE race
ADD CONSTRAINT fk_race_participant
FOREIGN KEY (participant_id) REFERENCES participant(id)
ON DELETE CASCADE
ON UPDATE CASCADE;
