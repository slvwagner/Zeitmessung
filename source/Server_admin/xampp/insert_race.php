<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: POST, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { http_response_code(204); exit; }

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung';

$REQUIRE_API_KEY = false;          // set to true to enforce
$SERVER_API_KEY  = 'change_me';

function respond($status, $data = null, $http = 200){
    http_response_code($http);
    echo json_encode(
        ['status' => $status, 'data' => $data],
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    );
    exit;
}

function error_out($msg, $http = 400, $extra = []){
    respond('error', array_merge(['message' => $msg], $extra), $http);
}

// Only POST allowed
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    error_out('Use POST', 405);
}

// Parse JSON body
$raw  = file_get_contents('php://input') ?: '';
$body = json_decode($raw, true);
if (!is_array($body)) {
    $body = [];
}

// Optional API key (query, JSON body, or header)
$api_key = $_GET['api_key']
    ?? ($body['api_key'] ?? ($_SERVER['HTTP_X_API_KEY'] ?? ''));

if ($REQUIRE_API_KEY && !hash_equals($SERVER_API_KEY, (string)$api_key)) {
    error_out('Invalid API key', 401);
}

// --- Required fields from firmware ---
$Startnummer     = isset($body['Startnummer']) ? (int)$body['Startnummer'] : null;
$run             = isset($body['run']) ? (int)$body['run'] : 1;
$timestamp_ms    = trim((string)($body['timestamp_ms'] ?? ''));  // "YYYY-MM-DD HH:MM:SS.mmm"
$race_status     = trim((string)($body['race_status'] ?? ''));
$device_id       = substr(trim((string)($body['device_id'] ?? '')), 0, 32);
$device_name     = substr(trim((string)($body['device_name'] ?? '')), 0, 50);
$timezone_offset = isset($body['timezone_offset']) ? (int)$body['timezone_offset'] : 0;

// --- Optional speed / beam fields from dual-beam logic ---
$speed_mps       = isset($body['speed_mps']) && $body['speed_mps'] !== ''
                   ? (float)$body['speed_mps']
                   : null;
$speed_kmh       = isset($body['speed_kmh']) && $body['speed_kmh'] !== ''
                   ? (float)$body['speed_kmh']
                   : null;
$beam_distance_mm = isset($body['beam_distance_mm']) && $body['beam_distance_mm'] !== ''
                    ? (float)$body['beam_distance_mm']
                    : null;

// Basic required-field check
if (!$Startnummer || !$run || $timestamp_ms === '' || $race_status === '' || $device_id === '' || $device_name === '') {
    error_out(
        'Missing one of: Startnummer, run, timestamp_ms, race_status, device_id, device_name',
        422
    );
}

// Quick format check for DATETIME(3)
if (!preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/', $timestamp_ms)) {
    error_out(
        'timestamp_ms must be "YYYY-MM-DD HH:MM:SS.mmm"',
        422,
        ['received' => $timestamp_ms]
    );
}

// DB connection
$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) {
    error_out('DB connect failed', 500, ['detail' => $mysqli->connect_error]);
}
$mysqli->set_charset('utf8mb4');

// New INSERT with optional speed fields
$sql = "INSERT INTO race
        (Startnummer, run, timestamp_ms,
         device_id, device_name, race_status,
         timezone_offset,
         speed_mps, speed_kmh, beam_distance_mm,
         last_updated)
        VALUES (?,?,?,?,?,?,?, ?,?,?, NOW(3))";

$stmt = $mysqli->prepare($sql);
if (!$stmt) {
    error_out('Prepare failed', 500, ['detail' => $mysqli->error]);
}

/*
 * Types:
 *  i = int
 *  s = string
 *  d = double/float
 *
 * Startnummer (i)
 * run (i)
 * timestamp_ms (s)
 * device_id (s)
 * device_name (s)
 * race_status (s)
 * timezone_offset (i)
 * speed_mps (d or NULL)
 * speed_kmh (d or NULL)
 * beam_distance_mm (d or NULL)
 */
$stmt->bind_param(
    'iissssiddd',
    $Startnummer,
    $run,
    $timestamp_ms,
    $device_id,
    $device_name,
    $race_status,
    $timezone_offset,
    $speed_mps,
    $speed_kmh,
    $beam_distance_mm
);


// NOTE: PHP will send NULLs for the float params if the variables are null
// and the columns must allow NULL.

$ok = $stmt->execute();
if (!$ok) {
    error_out('Insert failed', 500, ['detail' => $stmt->error]);
}
$stmt->close();

respond('success', ['id' => $mysqli->insert_id]);
