// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: POST, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { 
    http_response_code(204); 
    exit; 
}

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
    error_out('Invalid JSON body', 400);
}

// Optional API key (query, JSON body, or header)
$api_key = $_GET['api_key']
    ?? ($body['api_key'] ?? ($_SERVER['HTTP_X_API_KEY'] ?? ''));

if ($REQUIRE_API_KEY && !hash_equals($SERVER_API_KEY, (string)$api_key)) {
    error_out('Invalid API key', 401);
}

// --- Required field: name (e.g., "Rennstatus") ---
$name = trim((string)($body['name'] ?? ''));
if ($name === '') {
    error_out('Missing required field: name', 422);
}

// --- Required field: value ---
$value = trim((string)($body['value'] ?? ''));
if ($value === '') {
    error_out('Missing required field: value', 422);
}

// DB connection
$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) {
    error_out('DB connect failed', 500, ['detail' => $mysqli->connect_error]);
}
$mysqli->set_charset('utf8mb4');

// Check if the record exists
$check_sql = "SELECT COUNT(*) as count FROM race_management WHERE name = ?";
$check_stmt = $mysqli->prepare($check_sql);
if (!$check_stmt) {
    error_out('Prepare check failed', 500, ['detail' => $mysqli->error]);
}

$check_stmt->bind_param('s', $name);
$check_stmt->execute();
$check_result = $check_stmt->get_result();
$check_row = $check_result->fetch_assoc();
$check_stmt->close();

// Prepare SQL based on whether record exists
if ($check_row['count'] > 0) {
    // Update existing record
    $sql = "UPDATE race_management SET value = ?, updated_at = NOW() WHERE name = ?";
} else {
    // Insert new record (name is primary key, but we'll use INSERT IGNORE or handle duplicates)
    $sql = "INSERT INTO race_management (name, value) VALUES (?, ?) 
            ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW()";
}

$stmt = $mysqli->prepare($sql);
if (!$stmt) {
    error_out('Prepare failed', 500, ['detail' => $mysqli->error]);
}

$stmt->bind_param('ss', $value, $name);

$ok = $stmt->execute();
if (!$ok) {
    error_out('Operation failed', 500, ['detail' => $stmt->error]);
}

$affected_rows = $stmt->affected_rows;
$stmt->close();

// Get the updated/inserted record
$select_sql = "SELECT name, value, updated_at FROM race_management WHERE name = ?";
$select_stmt = $mysqli->prepare($select_sql);
$select_stmt->bind_param('s', $name);
$select_stmt->execute();
$result = $select_stmt->get_result();
$record = $result->fetch_assoc();
$select_stmt->close();

$mysqli->close();

respond('success', [
    'affected_rows' => $affected_rows,
    'record' => $record
]);
?>
