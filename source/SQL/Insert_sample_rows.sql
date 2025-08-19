
-- ==========================================
-- 1. Insert a participant
-- ==========================================
INSERT INTO participant (race_order, next_run, Name, Vorname)
VALUES (1, 1, 'Schmidt', 'Julia');

-- Store the generated participant ID
SET @participant_id = LAST_INSERT_ID();

-- Also store the run number from the participant table
SET @run_number = (SELECT next_run FROM participant WHERE id = @participant_id);

-- ==========================================
-- 2. StartGate inserts the race row
-- ==========================================
-- Example start timestamp
SET @start_time = '2025-08-19 10:00:00.000';

INSERT INTO race (participant_id, run, Start_timestamp, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @start_time, 'started', 'chip001', 'StartGate');

-- ==========================================
-- 3. FinishGate updates the same race row
-- ==========================================
-- End timestamp = start_time + 23.345s
SET @end_time = @start_time + INTERVAL 23.345 SECOND;

INSERT INTO race (participant_id, run, Start_timestamp, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @start_time, 'finished', 'chip001', 'FinishGate');

-- ==========================================
-- 4. Increment participant.next_run for future races
-- ==========================================
UPDATE participant
SET next_run = next_run + 1
WHERE id = @participant_id;

-- ==========================================
-- 5. Check result (optional)
-- ==========================================
SELECT * FROM participant;
SELECT * FROM race;
