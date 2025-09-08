-- ===== Fresh (re)create schema =====
CREATE DATABASE IF NOT EXISTS zeitmessung_V2
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE zeitmessung_V2;

-- ===== Tables =====

-- Teilnehmer
CREATE TABLE IF NOT EXISTS participant (
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

    -- RFID UID (little-endian hex like "5A:91:A7:AF")
    rfid_uid_le CHAR(11) NULL,
    UNIQUE KEY uniq_rfid_uid_le (rfid_uid_le),

    INDEX idx_Nickname (Nickname)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Renn-Events (Event-Log)
CREATE TABLE IF NOT EXISTS race (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    Startnummer INT UNSIGNED,
    run INT UNSIGNED DEFAULT 1,
    timestamp_ms DATETIME(3) NULL,
    device_id VARCHAR(32) NOT NULL,
    device_name VARCHAR(50) NOT NULL,
    race_status VARCHAR(50) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    timezone_offset INT DEFAULT 0,
    INDEX idx_race_status (race_status),
    INDEX idx_Startnummer (Startnummer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS registration_import_log (
  source                    VARCHAR(64)   NOT NULL,   -- e.g. 'hoststar:ch367079_race.participant'
  reg_key                   VARCHAR(64)   NOT NULL,   -- stable key for the remote row (id or fingerprint)
  imported_to_startnummer   INT           NOT NULL,
  imported_at               DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (source, reg_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ===== Foreign Keys =====
ALTER TABLE race
  ADD CONSTRAINT fk_race_participant
  FOREIGN KEY (Startnummer) REFERENCES participant(Startnummer)
  ON DELETE CASCADE
  ON UPDATE CASCADE;

-- ===== Helpful composite index for view performance =====
CREATE INDEX idx_run_status_ts ON race (Startnummer, run, race_status, timestamp_ms);

-- ===== Views =====
DROP VIEW IF EXISTS v_race_summary;

CREATE VIEW v_race_summary AS
SELECT
  t.Startnummer,
  t.Name,
  t.Vorname,
  t.run,
  t.start_time,
  t.finish_time,
  CASE
    WHEN t.start_time IS NULL OR t.finish_time IS NULL THEN NULL
    ELSE CAST(TIMESTAMPDIFF(MICROSECOND, t.start_time, t.finish_time) / 1000 AS DECIMAL(13,4))
  END AS duration_ms
FROM (
  SELECT
    p.Startnummer,
    p.Name,
    p.Vorname,
    r.run,
    -- earliest start per run
    MIN(CASE WHEN r.race_status IN ('started','start')   THEN r.timestamp_ms END) AS start_time,
    -- latest finish per run
    MAX(CASE WHEN r.race_status IN ('finished','finish') THEN r.timestamp_ms END) AS finish_time
  FROM participant p
  JOIN race r
    ON r.Startnummer = p.Startnummer
  GROUP BY p.Startnummer, p.Name, p.Vorname, r.run
) AS t;

-- (Optional) Completed-only view
DROP VIEW IF EXISTS v_race_summary_completed;
CREATE VIEW v_race_summary_completed AS
SELECT *
FROM v_race_summary
WHERE start_time IS NOT NULL AND finish_time IS NOT NULL;
