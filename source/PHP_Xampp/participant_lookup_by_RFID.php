<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
// (Optional) allow local development CORS; comment out on production if you like
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// -------------------------
// CONFIG: fill in your DB + API key
// -------------------------
$DB_HOST = 'localhost';          // e.g. 'lx51.hoststar.hosting' or '127.0.0.1'
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung_V2';
$REQUIRE_API_KEY = false;        // set true if you want to enforce a key
$SERVER_API_KEY  = 'change_me';  // set a strong value if $REQUIRE_API_KEY = true

// -------------------------
// Helper: uniform JSON reply
// -------------------------
function respond($status, $data = null, $http = 200) {
    http_response_code($http);
    echo json_encode(['status' => $status, 'data' => $data], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
function error_out($message, $http = 400, $extra = []) {
    respond('error', array_merge(['message' => $message], $extra), $http);
}

// -------------------------
// Parse input (GET or JSON POST)
// -------------------------
$raw = null;
$body = null;
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' &&
    isset($_SERVER['CONTENT_TYPE']) &&
    stripos($_SERVER['CONTENT_TYPE'], 'application/json') !== false) {
    $raw = file_get_contents('php://input') ?: '';
    $body = json_decode($raw, true);
    if (!is_array($body)) $body = [];
} else {
    $body = [];
}

// Accept rfid via ?rfid= or ?rfid_uid_le= or JSON { rfid_uid_le: "" }
$rfid = $_GET['rfid'] ?? $_GET['rfid_uid_le'] ?? ($body['rfid_uid_le'] ?? '');
$rfid = strtoupper(trim((string)$rfid));

// Optional API key via query, JSON or header
$api_key = $_GET['api_key'] ?? ($body['api_key'] ?? ($_SERVER['HTTP_X_API_KEY'] ?? ''));

// -------------------------
// Validate API key if required
// -------------------------
if ($REQUIRE_API_KEY) {
    if (!$api_key || !hash_equals($SERVER_API_KEY, (string)$api_key)) {
        error_out('Invalid or missing API key.', 401);
    }
}

// -------------------------
// Validate RFID format (your schema uses CHAR(11) like "5A:91:A7:AF")
// -------------------------
if ($rfid === '') {
    error_out('Missing parameter rfid_uid_le. Provide ?rfid=AA:BB:CC:DD or JSON {"rfid_uid_le": "AA:BB:CC:DD"}');
}
if (!preg_match('/^[0-9A-F]{2}(:[0-9A-F]{2}){3}$/', $rfid)) {
    error_out('Invalid rfid_uid_le format. Expected like "5A:91:A7:AF" (4 bytes, colon-separated, uppercase hex).', 422, ['received' => $rfid]);
}

// -------------------------
// Connect DB (mysqli + prepared statements)
// -------------------------
$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) {
    error_out('Database connection failed', 500, ['detail' => $mysqli->connect_error]);
}
$mysqli->set_charset('utf8mb4');

// -------------------------
// Query: participant by RFID + latest race (if any)
// -------------------------
// 1) Find participant
$sqlP = "SELECT 
            Startnummer, created_at, last_updated, race_order, last_run, next_run,
            Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum, Gewicht,
            rfid_uid_le
         FROM participant
         WHERE rfid_uid_le = ?";
$stmtP = $mysqli->prepare($sqlP);
if (!$stmtP) error_out('Failed to prepare participant query', 500, ['detail' => $mysqli->error]);
$stmtP->bind_param('s', $rfid);
$stmtP->execute();
$resP = $stmtP->get_result();
$participant = $resP->fetch_assoc();
$stmtP->close();

if (!$participant) {
    // Still 200 OK but no data, so clients can treat gracefully
    respond('ok', ['participant' => null, 'race' => null, 'rfid_uid_le' => $rfid]);
}

// 2) Latest race row for that Startnummer (if any). We order by timestamp_ms (NULLS last) and id
$sqlR = "SELECT 
            r.id            AS race_id,
            r.Startnummer   AS Startnummer,
            r.run           AS run,
            DATE_FORMAT(r.timestamp_ms, '%Y-%m-%d %H:%i:%s.%f') AS timestamp_ms,
            r.device_id, r.device_name, r.race_status,
            DATE_FORMAT(r.created_at, '%Y-%m-%d %H:%i:%s.%f') AS created_at,
            DATE_FORMAT(r.last_updated, '%Y-%m-%d %H:%i:%s.%f') AS last_updated,
            r.timezone_offset
         FROM race r
         WHERE r.Startnummer = ?
         ORDER BY 
            r.timestamp_ms IS NULL,  -- puts non-NULL first
            r.timestamp_ms DESC,
            r.id DESC
         LIMIT 1";
$stmtR = $mysqli->prepare($sqlR);
if (!$stmtR) error_out('Failed to prepare race query', 500, ['detail' => $mysqli->error]);
$stmtR->bind_param('i', $participant['Startnummer']);
$stmtR->execute();
$resR = $stmtR->get_result();
$race = $resR->fetch_assoc();
$stmtR->close();

// -------------------------
// Reply
// -------------------------
respond('ok', [
    'rfid_uid_le' => $rfid,
    'participant' => $participant,
    'race'        => $race ?: null
]);
