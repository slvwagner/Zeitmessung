-- ===== Fresh (re)create schema =====
DROP DATABASE IF EXISTS zeitmessung;
CREATE DATABASE zeitmessung
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE zeitmessung;

-- ===== Tables =====

-- Teilnehmer
CREATE TABLE participant (
    Startnummer INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    last_run INT DEFAULT NULL,
    next_run INT DEFAULT 1,
    Name VARCHAR(100) DEFAULT '',
    Vorname VARCHAR(100) DEFAULT '',
    Nickname VARCHAR(100) DEFAULT '',
    Phone VARCHAR(50) DEFAULT '',
    `E-mail` VARCHAR(50) DEFAULT '',
    Kategorie VARCHAR(255) DEFAULT '',
    Geburtsdatum DATE NOT NULL DEFAULT (CURRENT_DATE),

    -- RFID UID (little-endian hex like "5A:91:A7:AF")
    rfid_uid_le CHAR(11) NULL,
    UNIQUE KEY uniq_rfid_uid_le (rfid_uid_le),

    INDEX idx_rfid_uid_le (rfid_uid_le)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Renn-Events (Event-Log)
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
    timezone_offset INT DEFAULT 0,
    speed_mps FLOAT NULL,
    speed_kmh FLOAT NULL,
    beam_distance_mm FLOAT NULL,
    INDEX idx_race_status (race_status),
    INDEX idx_Startnummer (Startnummer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ===== Foreign Keys =====
ALTER TABLE race
  ADD CONSTRAINT fk_race_participant
  FOREIGN KEY (Startnummer) REFERENCES participant(Startnummer)
  ON DELETE CASCADE
  ON UPDATE CASCADE;

-- Import-Log
CREATE TABLE Picolog (
  Device_ID     VARCHAR(64)   NOT NULL,   -- Device ID
  Device_Name   VARCHAR(64)   NOT NULL,   -- Device Name
  log           VARCHAR(256)   NOT NULL,   -- actual log information 
  created_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS race_management  (
  name  VARCHAR(64) PRIMARY KEY,
  value VARCHAR(64) NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- race management
INSERT INTO race_management(name, value) VALUES
  ("Rennstatus","0") -- "running" or "stoped"
ON DUPLICATE KEY UPDATE value=VALUES(value);

-- Setting   
CREATE TABLE IF NOT EXISTS system_settings (
  name  VARCHAR(64) PRIMARY KEY,
  value VARCHAR(64) NOT NULL,
  unit VARCHAR(64) NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Settings
INSERT INTO system_settings(name, value, unit) VALUES
  ("relock cooldown time","30", "s")
ON DUPLICATE KEY UPDATE value=VALUES(value);

INSERT INTO system_settings(name, value, unit) VALUES
  ("track_headway time","60","s")
ON DUPLICATE KEY UPDATE value=VALUES(value);

INSERT INTO system_settings(name, value, unit) VALUES
  ("beam distance","43.18","mm")
ON DUPLICATE KEY UPDATE value=VALUES(value);

INSERT INTO system_settings(name, value, unit) VALUES
  ("beam pair timeout","2000","ms")
ON DUPLICATE KEY UPDATE value=VALUES(value);

INSERT INTO system_settings(name, value, unit) VALUES
  ("LOCAL_TIME_OFFSET","1","h")
ON DUPLICATE KEY UPDATE value=VALUES(value);


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
    ELSE CONCAT(
      LPAD(FLOOR(t.total_ms / 3600000), 2, '0'), ':',                         -- hours
      LPAD(FLOOR(MOD(t.total_ms, 3600000) / 60000), 2, '0'), ':',             -- minutes
      LPAD(FLOOR(MOD(t.total_ms, 60000) / 1000), 2, '0'), '.',                -- seconds
      LPAD(MOD(t.total_ms, 1000), 3, '0')                                     -- milliseconds
    )
  END AS duration_hms
FROM (
  SELECT
    p.Startnummer,
    p.Name,
    p.Vorname,
    r.run,
    MIN(CASE WHEN r.race_status IN ('started','start')   THEN r.timestamp_ms END) AS start_time,
    MAX(CASE WHEN r.race_status IN ('finished','finish') THEN r.timestamp_ms END) AS finish_time,
    (TIMESTAMPDIFF(MICROSECOND,
                   MIN(CASE WHEN r.race_status IN ('started','start')   THEN r.timestamp_ms END),
                   MAX(CASE WHEN r.race_status IN ('finished','finish') THEN r.timestamp_ms END)
     ) DIV 1000) AS total_ms
  FROM participant p
  JOIN race r
    ON r.Startnummer = p.Startnummer
  GROUP BY p.Startnummer, p.Name, p.Vorname, r.run
) AS t;

-- Optional: Completed-only runs
DROP VIEW IF EXISTS v_race_summary_completed;
CREATE VIEW v_race_summary_completed AS
SELECT *
FROM v_race_summary
WHERE start_time IS NOT NULL AND finish_time IS NOT NULL;
