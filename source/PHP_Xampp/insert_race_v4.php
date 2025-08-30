<?php
// insert_race_v4.php — inserts a row into `race` (normalized headers + debug)

header('Content-Type: application/json; charset=UTF-8');

// ===== CONFIG =====
$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung_V2';
$DB_USER = 'root';
$DB_PASS = '';

$API_KEY = '8f1f3b0b9d2a4d1c7e88a9b9f1a2c3d4e5f60718293a4b5c6d7e8f9012345678';

// ===== HELPERS =====
function json_error($http, $msg, $extra = []) {
    http_response_code($http);
    echo json_encode(['ok' => false, 'error' => $msg] + $extra, JSON_UNESCAPED_UNICODE);
    exit;
}

function read_input() {
    $raw = file_get_contents('php://input');
    $ct  = $_SERVER['CONTENT_TYPE'] ?? '';
    if (stripos((string)$ct, 'application/json') !== false) {
        $data = json_decode($raw, true);
        if (!is_array($data)) $data = [];
        return $data;
    }
    return $_POST;
}

// Case-insensitive header fetch; also checks $_SERVER fallback
function get_header_ci($name) {
    $want = strtolower($name);
    $headers = function_exists('getallheaders') ? getallheaders() : [];
    foreach ($headers as $k => $v) {
        if (strtolower($k) === $want) return (string)$v;
    }
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return isset($_SERVER[$key]) ? (string)$_SERVER[$key] : '';
}

function get_param($arr, $key, $default = null) {
    return isset($arr[$key]) ? trim((string)$arr[$key]) : $default;
}

function to_mysql_datetime_ms($val) {
    if ($val === null || $val === '') return null;

    if (preg_match('/^\d{12,}$/', (string)$val)) {
        $ms = (float)$val;
        $sec = floor($ms / 1000);
        $frac_ms = $ms - ($sec * 1000);
        $dt = DateTime::createFromFormat('U.u', sprintf('%.3f', $sec + ($frac_ms/1000.0)));
        if (!$dt) return null;
        $dt->setTimezone(new DateTimeZone('UTC'));
        return $dt->format('Y-m-d H:i:s.u');
    }
    try {
        $dt = new DateTime((string)$val);
        $dt->setTimezone(new DateTimeZone('UTC'));
        return $dt->format('Y-m-d H:i:s.u');
    } catch (Exception $e) {
        return null;
    }
}

// ===== PING (to confirm correct file) =====
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET' && isset($_GET['ping'])) {
    $hdrs = function_exists('getallheaders') ? getallheaders() : [];
    echo json_encode([
        'ok' => true,
        'msg' => 'insert_race_v4 alive',
        'version' => 'v4',
        'headers_seen' => $hdrs,
        'server_HTTP_X_API_KEY' => $_SERVER['HTTP_X_API_KEY'] ?? null
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// ===== METHOD =====
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    json_error(405, 'Use POST');
}

// ===== AUTH (header OR JSON OR GET — during debug) =====
$received = get_header_ci('X-API-Key');
$in = read_input();
if ($received === '' && isset($in['api_key'])) { // DEBUG fallback
    $received = (string)$in['api_key'];
}
if ($received === '' && isset($_GET['api_key'])) { // DEBUG fallback
    $received = (string)$_GET['api_key'];
}

if (!hash_equals($API_KEY, $received)) {
    // Full debug so we can see what's going on
    $hdrs = function_exists('getallheaders') ? getallheaders() : [];
    json_error(401, 'Unauthorized (bad API key)', [
        'received'      => $received,
        'received_hex'  => bin2hex($received),
        'expected_hex'  => bin2hex($API_KEY),
        'received_len'  => strlen($received),
        'expected_len'  => strlen($API_KEY),
        'headers'       => $hdrs,
        'server_HTTP_X_API_KEY' => $_SERVER['HTTP_X_API_KEY'] ?? null
    ]);
}

// ===== INPUT =====
$Startnummer = get_param($in, 'Startnummer');
$device_id   = get_param($in, 'device_id');
$device_name = get_param($in, 'device_name');
$race_status = get_param($in, 'race_status');
$run         = get_param($in, 'run', '1');
$timestamp   = get_param($in, 'timestamp_ms');

if ($Startnummer === null || $Startnummer === '' || !ctype_digit($Startnummer) || (int)$Startnummer < 1) {
    json_error(400, 'Startnummer must be a positive integer');
}
if ($run === null || $run === '' || !ctype_digit($run) || (int)$run < 1) {
    json_error(400, 'run must be a positive integer');
}
if ($device_id === null || $device_id === '') {
    json_error(400, 'device_id is required');
}
if ($device_name === null || $device_name === '') {
    json_error(400, 'device_name is required');
}
if ($race_status === null || $race_status === '') {
    json_error(400, 'race_status is required');
}

if (strlen($device_id) > 32)     $device_id = substr($device_id, 0, 32);
if (strlen($device_name) > 50)   $device_name = substr($device_name, 0, 50);
if (strlen($race_status) > 50)   $race_status = substr($race_status, 0, 50);

$timestamp_mysql = to_mysql_datetime_ms($timestamp);

// ===== DB =====
try {
    $pdo = new PDO(
        "mysql:host={$DB_HOST};dbname={$DB_NAME};charset=utf8mb4",
        $DB_USER,
        $DB_PASS,
        [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );

    $check = $pdo->prepare("SELECT 1 FROM participant WHERE Startnummer = ?");
    $check->execute([(int)$Startnummer]);
    if (!$check->fetch()) {
        json_error(404, 'Participant (Startnummer) not found', ['Startnummer' => (int)$Startnummer]);
    }

    if ($timestamp_mysql === null) {
        $sql = "INSERT INTO race (Startnummer, run, timestamp_ms, device_id, device_name, race_status)
                VALUES (?, ?, NULL, ?, ?, ?)";
        $args = [(int)$Startnummer, (int)$run, $device_id, $device_name, $race_status];
    } else {
        $sql = "INSERT INTO race (Startnummer, run, timestamp_ms, device_id, device_name, race_status)
                VALUES (?, ?, ?, ?, ?, ?)";
        $args = [(int)$Startnummer, (int)$run, $timestamp_mysql, $device_id, $device_name, $race_status];
    }

    $stmt = $pdo->prepare($sql);
    $stmt->execute($args);

    $id = (int)$pdo->lastInsertId();

    echo json_encode([
        'ok'           => true,
        'id'           => $id,
        'Startnummer'  => (int)$Startnummer,
        'run'          => (int)$run,
        'timestamp_ms' => $timestamp_mysql,
        'device_id'    => $device_id,
        'device_name'  => $device_name,
        'race_status'  => $race_status,
    ], JSON_UNESCAPED_UNICODE);

} catch (PDOException $e) {
    json_error(500, 'DB error', ['detail' => $e->getMessage()]);
}


?>
