<?php
// api/race_create.php
header('Content-Type: application/json');
ini_set('display_errors', 0);

// --- DB CONFIG ---
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

// Use mysqli exceptions for cleaner error handling
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

function now_datetime_ms(): string {
    // Returns "YYYY-mm-dd HH:ii:ss.mmm"
    $dt = DateTime::createFromFormat('U.u', sprintf('%.6F', microtime(true)));
    $dt->setTimezone(new DateTimeZone(date_default_timezone_get()));
    return $dt->format('Y-m-d H:i:s.v'); // 'v' works with DateTime::format
}

try {
    // --- CONNECT ---
    $conn = new mysqli($servername, $username, $password, $dbname);
    $conn->set_charset('utf8mb4');

    // --- READ JSON BODY ---
    $raw = file_get_contents('php://input');
    if ($raw === '' || $raw === false) {
        throw new Exception("No input data received");
    }
    $data = json_decode($raw, true, 512, JSON_BIGINT_AS_STRING);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception("Invalid JSON data");
    }

    // --- INPUTS (with defaults) ---
    // All fields are optional except device_id/device_name/race_status (NOT NULL by schema)
    $participant_id = $data['participant_id'] ?? null;                  // int or null
    $run            = $data['run']            ?? 1;                      // int, default 1
    $timestamp_ms   = array_key_exists('timestamp_ms', $data)
                        ? $data['timestamp_ms']                           // string or null/empty
                        : now_datetime_ms();                              // default: now with ms
    $device_id      = $data['device_id']      ?? 'unknown';
    $device_name    = $data['device_name']    ?? 'unnamed';
    $race_status    = $data['race_status']    ?? 'UNKNOWN';

    // --- BASIC VALIDATION ---
    if ($participant_id !== null && !is_numeric($participant_id)) {
        throw new Exception("participant_id must be an integer or null");
    }
    $participant_id = $participant_id !== null ? (int)$participant_id : null;

    if (!is_numeric($run)) {
        throw new Exception("run must be an integer");
    }
    $run = (int)$run;
    if ($run < 1) {
        throw new Exception("run must be >= 1");
    }

    // timestamp_ms: allow null, "YYYY-MM-DD", "YYYY-MM-DD HH:MM:SS[.mmm]"
    if ($timestamp_ms === '' || $timestamp_ms === null) {
        $timestamp_ms = null;
    } else {
        $pattern = '/^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)?$/';
        if (!preg_match($pattern, (string)$timestamp_ms)) {
            throw new Exception("timestamp_ms must be 'YYYY-MM-DD[ HH:MM:SS[.mmm]]' or null");
        }
        // Normalize to .mmm if only seconds provided (optional)
        // (DB accepts plain seconds too, so we leave as-is.)
    }

    if (!is_string($device_id) || $device_id === '') {
        throw new Exception("device_id is required");
    }
    if (strlen($device_id) > 32) {
        throw new Exception("device_id too long (max 32)");
    }

    if (!is_string($device_name) || $device_name === '') {
        throw new Exception("device_name is required");
    }
    if (strlen($device_name) > 50) {
        throw new Exception("device_name too long (max 50)");
    }

    if (!is_string($race_status) || $race_status === '') {
        throw new Exception("race_status is required");
    }
    if (strlen($race_status) > 50) {
        throw new Exception("race_status too long (max 50)");
    }

    // --- OPTIONAL DEBUG LOG ---
    // file_put_contents('Zeitmessung_write_debug.log', "[".date('Y-m-d H:i:s')."] Received: ".print_r($data, true).PHP_EOL, FILE_APPEND);

    // --- BUILD PREPARED INSERT ---
    // We branch to handle nullable participant_id and timestamp_ms cleanly.
    $sql = "INSERT INTO race (participant_id, run, timestamp_ms, device_id, device_name, race_status)
            VALUES (?, ?, ?, ?, ?, ?)";
    $stmt = $conn->prepare($sql);

    // Bind with correct NULL handling
    // For NULL DATETIME/INT we must use bind_param but pass null by reference & set types accordingly.
    // We'll use 'isssss' conditionally; when null, we still bind as types but pass null.
    // Types: i (participant_id), i (run), s (timestamp_ms), s, s, s
    $stmt->bind_param(
        'iissss',
        $participant_id,   // null allowed
        $run,
        $timestamp_ms,     // null allowed
        $device_id,
        $device_name,
        $race_status
    );

    // If participant_id is null, ensure it is actually null (mysqli will send NULL)
    if ($participant_id === null) {
        $stmt->send_long_data(0, null); // harmless; ensures null stays null
    }
    if ($timestamp_ms === null) {
        $stmt->send_long_data(2, null);
    }

    $stmt->execute();

    echo json_encode([
        "status" => "success",
        "message" => "Race row created",
        "id" => $conn->insert_id
    ], JSON_UNESCAPED_UNICODE);

    $stmt->close();
    $conn->close();

} catch (Throwable $e) {
    http_response_code(400);
    echo json_encode([
        "status"  => "error",
        "message" => $e->getMessage()
    ]);
}
