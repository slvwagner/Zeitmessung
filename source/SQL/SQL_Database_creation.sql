CREATE DATABASE zeitmessung_V2;
USE zeitmessung_V2;

CREATE TABLE participant (
    Startnummer INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    race_order INT DEFAULT NULL,
    last_run INT DEFAULT NULL,
    next_run INT DEFAULT 1,
    Name VARCHAR(100) DEFAULT '',
    Vorname VARCHAR(100) DEFAULT '',
    Nickname VARCHAR(100) DEFAULT '',
    Phone VARCHAR(50) DEFAULT '',
    `E-mail` VARCHAR(50) DEFAULT '',
    Kategorie VARCHAR(255) DEFAULT '',
    Geburtsdatum DATE NOT NULL DEFAULT (CURRENT_DATE),
    Gewicht DOUBLE DEFAULT NULL,
    INDEX idx_Nickname (Nickname)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE race (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    Startnummer INT UNSIGNED,
    run INT UNSIGNED DEFAULT 1,
    timestamp_ms DATETIME(3) NULL,
    device_id VARCHAR(32) NOT NULL,
    device_name VARCHAR(50) NOT NULL,
    race_status VARCHAR(50) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_race_status (race_status),
    INDEX idx_Startnummer (Startnummer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE race
ADD CONSTRAINT fk_race_participant
FOREIGN KEY (Startnummer) REFERENCES participant(Startnummer)
ON DELETE CASCADE
ON UPDATE CASCADE;
