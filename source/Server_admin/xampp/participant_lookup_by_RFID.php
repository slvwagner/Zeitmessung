// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.2';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
declare(strict_types=1);

// Enable error reporting for debugging
error_reporting(E_ALL);
ini_set('display_errors', '1');
ini_set('display_startup_errors', '1');
ini_set('log_errors', '1');

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { 
    http_response_code(204); 
    exit; 
}

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung';

$REQUIRE_API_KEY = false;
$SERVER_API_KEY  = 'change_me';

function respond($status, $data=null, $http=200) {
    http_response_code($http);
    echo json_encode(['status'=>$status,'data'=>$data], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
    exit;
}

function error_out($m, $h=400, $x=[]) { 
    respond('error', array_merge(['message'=>$m],$x), $h); 
}

/* ---- Read input (GET or JSON POST) ---- */
$raw = null; 
$body = null;

// Log the request method for debugging
error_log("Request method: " . ($_SERVER['REQUEST_METHOD'] ?? 'NULL'));

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST'
    && isset($_SERVER['CONTENT_TYPE'])
    && stripos($_SERVER['CONTENT_TYPE'], 'application/json') !== false) {
    $raw  = file_get_contents('php://input') ?: '';
    error_log("Raw POST data: " . $raw);
    $body = json_decode($raw, true) ?: [];
} else {
    $body = [];
}

// Get RFID from all possible sources
$rfid = $_GET['rfid'] ?? $_GET['rfid_uid_le'] ?? ($body['rfid_uid_le'] ?? ($body['rfid'] ?? ''));
$rfid = strtoupper(trim((string)$rfid));

error_log("Received RFID: " . ($rfid ?: 'EMPTY'));

// API key check (optional)
$api_key = $_GET['api_key'] ?? ($body['api_key'] ?? ($_SERVER['HTTP_X_API_KEY'] ?? ''));
if ($REQUIRE_API_KEY && !hash_equals($SERVER_API_KEY, (string)$api_key)) {
    error_out('Invalid API key', 401);
}

// Validate RFID
if ($rfid === '') {
    error_out('Missing rfid param (?rfid=AA:BB:CC:DD)', 422);
}
if (!preg_match('/^[0-9A-F]{2}(:[0-9A-F]{2}){3}$/', $rfid)) {
    error_out('Bad RFID format', 422, ['received'=>$rfid]);
}

/* ---- DB Connection ---- */
try {
    $mysqli = new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
    if ($mysqli->connect_errno) {
        throw new Exception('DB connect failed: ' . $mysqli->connect_error);
    }
    $mysqli->set_charset('utf8mb4');
    
    // Test connection
    if (!$mysqli->ping()) {
        throw new Exception('DB connection lost');
    }
    
    error_log("Database connection successful");
    
} catch (Exception $e) {
    error_out('Database error', 500, ['detail' => $e->getMessage()]);
}

/* ---- Participant by RFID ---- */
try {
    $sql = "SELECT 
              Startnummer, created_at, last_updated, last_run, next_run,
              Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum,
              rfid_uid_le
            FROM participant
            WHERE rfid_uid_le = ?
            LIMIT 1";
    
    error_log("Preparing SQL: " . $sql);
    error_log("With RFID: " . $rfid);
    
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        throw new Exception('Prepare failed: ' . $mysqli->error);
    }
    
    $stmt->bind_param('s', $rfid);
    
    if (!$stmt->execute()) {
        throw new Exception('Execute failed: ' . $stmt->error);
    }
    
    $res = $stmt->get_result();
    if (!$res) {
        throw new Exception('Get result failed: ' . $stmt->error);
    }
    
    $participant = $res->fetch_assoc();
    $stmt->close();
    
    error_log("Participant found: " . ($participant ? 'YES' : 'NO'));
    
} catch (Exception $e) {
    $mysqli->close();
    error_out('Query error', 500, ['detail' => $e->getMessage(), 'sql' => $sql ?? 'N/A', 'rfid' => $rfid]);
}

if (!$participant) {
    $mysqli->close();
    respond('ok', [
        'participant'     => null,
        'rfid_uid_le'     => $rfid,
        'on_track'        => false,
        'current_run'     => null,
        'allowed_to_lock' => false,
        'reason'          => 'RFID not assigned'
    ]);
}

/* ---- On-track check ---- */
try {
    $snr = (int)$participant['Startnummer'];
    
    $sql_active = "
    SELECT  s.run AS current_run, MIN(s.timestamp_ms) AS started_at
    FROM    race s
    LEFT JOIN race f
           ON f.Startnummer = s.Startnummer
          AND f.run         = s.run
          AND f.race_status IN ('finished','time confirmed')
    WHERE   s.Startnummer   = ?
      AND   s.race_status   = 'started'
      AND   f.id IS NULL           -- no finishing event found for that run
    GROUP BY s.run
    ORDER BY started_at DESC
    LIMIT 1
    ";
    
    error_log("Preparing active check SQL for Startnummer: " . $snr);
    
    $stmt = $mysqli->prepare($sql_active);
    if (!$stmt) {
        throw new Exception('Prepare (active check) failed: ' . $mysqli->error);
    }
    
    $stmt->bind_param('i', $snr);
    
    if (!$stmt->execute()) {
        throw new Exception('Execute (active check) failed: ' . $stmt->error);
    }
    
    $res = $stmt->get_result();
    $active = $res->fetch_assoc();
    $stmt->close();
    
    error_log("Active check result: " . ($active ? 'ON_TRACK' : 'NOT_ON_TRACK'));
    
} catch (Exception $e) {
    error_log("Error in active check: " . $e->getMessage());
    // If there's an error in the active check, assume not on track
    $active = null;
}

$on_track        = (bool)$active;
$current_run     = $active ? (int)$active['current_run'] : null;
$allowed_to_lock = !$on_track;

$mysqli->close();

/* ---- Reply ---- */
respond('ok', [
    'participant'     => $participant,
    'rfid_uid_le'     => $rfid,
    'on_track'        => $on_track,
    'current_run'     => $current_run,
    'allowed_to_lock' => $allowed_to_lock,
    'reason'          => $on_track ? 'Racer is currently on track' : 'Allowed',
    'debug'           => [
        'startnummer' => $participant['Startnummer'],
        'name'        => $participant['Name'] . ' ' . $participant['Vorname'],
        'active_check_success' => isset($active) ? true : false
    ]
]);
?>



