
-- ==========================================
-- 1. Insert participant #1
-- ==========================================
INSERT INTO participant (race_order, next_run, Name, Vorname)
VALUES (0, 1, 'Schmidt', 'Julia');

SET @Startnummer      = LAST_INSERT_ID();
SET @run_number       = (SELECT next_run FROM participant WHERE Startnummer = @Startnummer);
SET @timestamp_ms_init = NOW(3);             -- start time for this run

-- 2. StartGate
INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@Startnummer, @run_number, @timestamp_ms_init, 'started', 'chip001', 'StartGate');

-- 3. InterimGate 1 (start + 45.345s)
SET @timestamp_ms = @timestamp_ms_init + INTERVAL 45.345 SECOND;
INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@Startnummer, @run_number, @timestamp_ms, 'interim 1', 'chip002', 'InterimGate 1');

-- 4. FinishGate (interim + 50.745s)
SET @timestamp_ms = @timestamp_ms + INTERVAL 50.745 SECOND;
INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@Startnummer, @run_number, @timestamp_ms, 'finished', 'chip003', 'FinishGate');

-- Increment next_run for participant #1
UPDATE participant
SET next_run = next_run + 1, last_run = COALESCE(last_run,0) + 1
WHERE Startnummer = @Startnummer;

-- ==========================================
-- 1. Insert participant #2
-- ==========================================
INSERT INTO participant (race_order, next_run, Name, Vorname)
VALUES (1, 1, 'Florian', 'Wagner');

SET @Startnummer       = LAST_INSERT_ID();
SET @run_number        = (SELECT next_run FROM participant WHERE Startnummer = @Startnummer);
SET @timestamp_ms_init = NOW(3);             -- new baseline for this participant

-- 2. StartGate (baseline + 31.24s)
SET @timestamp_ms = @timestamp_ms_init + INTERVAL 31.24 SECOND;
INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@Startnummer, @run_number, @timestamp_ms, 'started', 'chip001', 'StartGate');

-- 3. Judge disqualification (+ 25.624s after start)
SET @timestamp_ms = @timestamp_ms + INTERVAL 25.624 SECOND;
INSERT INTO race (Startnummer, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@Startnummer, @run_number, @timestamp_ms, 'disqualify', 'Computer client', 'Judge');

-- Increment next_run for participant #2
UPDATE participant
SET next_run = next_run + 1, last_run = COALESCE(last_run,0) + 1
WHERE Startnummer = @Startnummer;

-- ==========================================
-- 1. Insert participant #1
-- ==========================================
INSERT INTO participant (race_order, next_run, Name, Vorname)
VALUES (2, 1, 'Wagner', 'Nadia');

SET @Startnummer      = LAST_INSERT_ID();
SET @run_number       = (SELECT next_run FROM participant WHERE Startnummer = @Startnummer);
