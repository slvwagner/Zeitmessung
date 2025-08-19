
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
SET @timestamp_ms_init = NOW(3);

INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @timestamp_ms_init, 'started', 'chip001', 'StartGate');

-- ==========================================
-- 3. FinishGate updates the same race row
-- ==========================================
SET @timestamp_ms = @timestamp_ms_init + INTERVAL 45.345 SECOND;

INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @timestamp_ms, 'interim 1', 'chip002', 'InterimGate 1');


-- ==========================================
-- 4. FinishGate updates the same race row
-- ==========================================
-- End timestamp = timestamp_ms + 23.345s
SET @timestamp_ms = @timestamp_ms + INTERVAL 50.745 SECOND;

INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @timestamp_ms, 'finished', 'chip003', 'FinishGate');

-- ==========================================
-- 4. Increment participant.next_run for future races
-- ==========================================
UPDATE participant
SET next_run = next_run + 1
WHERE id = @participant_id;



-- ==========================================
-- 1. Insert a participant
-- ==========================================
INSERT INTO participant (race_order, next_run, Name, Vorname)
VALUES (1, 1, 'Florian', 'Wagner');

-- Store the generated participant ID
SET @participant_id = LAST_INSERT_ID();

-- Also store the run number from the participant table
SET @run_number = (SELECT next_run FROM participant WHERE id = @participant_id);

-- ==========================================
-- 2. StartGate inserts race row-- ==========================================
-- Example start timestamp
SET @timestamp_ms = timestamp_ms_init + INTERVAL 31.24 SECOND;

INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @timestamp_ms_init, 'started', 'chip001', 'StartGate');


-- ==========================================
-- 3. Disqualifed by judge updatinsert race row
-- ==========================================
-- End timestamp = timestamp_ms + 23.345s
SET @timestamp_ms = @timestamp_ms + INTERVAL 25.624 SECOND;

INSERT INTO race (participant_id, run, timestamp_ms, race_status, device_id, device_name)
VALUES (@participant_id, @run_number, @timestamp_ms, 'disqualify', 'Computer client', 'Judge');

-- ==========================================
-- 4. Increment participant.next_run for future races
-- ==========================================
UPDATE participant
SET next_run = next_run + 1
WHERE id = @participant_id;



