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

// --- Required fields for Picolog ---
$Device_ID     = substr(trim((string)($body['Device_ID'] ?? '')), 0, 64);
$Device_Name   = substr(trim((string)($body['Device_Name'] ?? '')), 0, 64);
$log           = substr(trim((string)($body['log'] ?? '')), 0, 256);

// Optional timestamp override (for historical logs)
$created_at    = trim((string)($body['created_at'] ?? ''));

// Basic required-field check
if ($Device_ID === '' || $Device_Name === '' || $log === '') {
    error_out(
        'Missing one of: Device_ID, Device_Name, log',
        422
    );
}

// Validate optional timestamp format if provided
if ($created_at !== '') {
    // Check if it matches DATETIME(3) format: YYYY-MM-DD HH:MM:SS.mmm
    if (!preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$/', $created_at)) {
        error_out(
            'created_at must be "YYYY-MM-DD HH:MM:SS.mmm" or empty to use current timestamp',
            422,
            ['received' => $created_at]
        );
    }
}

// DB connection
$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) {
    error_out('DB connect failed', 500, ['detail' => $mysqli->connect_error]);
}
$mysqli->set_charset('utf8mb4');

// Prepare SQL statement
// created_at will use DEFAULT CURRENT_TIMESTAMP(3) if not provided
if ($created_at !== '') {
    $sql = "INSERT INTO Picolog (Device_ID, Device_Name, log, created_at) VALUES (?, ?, ?, ?)";
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        error_out('Prepare failed', 500, ['detail' => $mysqli->error]);
    }
    $stmt->bind_param('ssss', $Device_ID, $Device_Name, $log, $created_at);
} else {
    $sql = "INSERT INTO Picolog (Device_ID, Device_Name, log) VALUES (?, ?, ?)";
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        error_out('Prepare failed', 500, ['detail' => $mysqli->error]);
    }
    $stmt->bind_param('sss', $Device_ID, $Device_Name, $log);
}

$ok = $stmt->execute();
if (!$ok) {
    error_out('Insert failed', 500, ['detail' => $stmt->error]);
}
$stmt->close();

respond('success', [
    'id' => $mysqli->insert_id,
    'message' => 'Log entry created successfully'
]);

// Optional: close connection (not strictly necessary as PHP will close it automatically)
$mysqli->close();
?>
