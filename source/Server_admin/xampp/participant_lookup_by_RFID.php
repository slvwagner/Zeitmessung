<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
ini_set('log_errors', '1');

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { http_response_code(204); exit; }

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
function error_out($m,$h=400,$x=[]) { respond('error', array_merge(['message'=>$m],$x), $h); }

/* ---- Read input (GET or JSON POST) ---- */
$raw = null; $body = null;
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST'
    && isset($_SERVER['CONTENT_TYPE'])
    && stripos($_SERVER['CONTENT_TYPE'], 'application/json') !== false) {
  $raw  = file_get_contents('php://input') ?: '';
  $body = json_decode($raw, true) ?: [];
} else {
  $body = [];
}

$rfid = $_GET['rfid'] ?? $_GET['rfid_uid_le'] ?? ($body['rfid_uid_le'] ?? '');
$rfid = strtoupper(trim((string)$rfid));

$api_key = $_GET['api_key'] ?? ($body['api_key'] ?? ($_SERVER['HTTP_X_API_KEY'] ?? ''));
if ($REQUIRE_API_KEY && !hash_equals($SERVER_API_KEY, (string)$api_key)) error_out('Invalid API key', 401);

if ($rfid === '') error_out('Missing rfid param (?rfid=AA:BB:CC:DD)', 422);
if (!preg_match('/^[0-9A-F]{2}(:[0-9A-F]{2}){3}$/', $rfid)) error_out('Bad RFID format', 422, ['received'=>$rfid]);

/* ---- DB ---- */
$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
$mysqli->set_charset('utf8mb4');

/* ---- Participant by RFID ---- */
$sql = "SELECT 
          Startnummer, created_at, last_updated, last_run, next_run,
          Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum,
          rfid_uid_le
        FROM participant
        WHERE rfid_uid_le = ?
        LIMIT 1";
$stmt = $mysqli->prepare($sql);
if (!$stmt) error_out('Prepare failed', 500, ['detail'=>$mysqli->error]);
$stmt->bind_param('s', $rfid);
$stmt->execute();
$res = $stmt->get_result();
$participant = $res->fetch_assoc();
$stmt->close();

if (!$participant) {
  respond('ok', [
    'participant'     => null,
    'rfid_uid_le'     => $rfid,
    'on_track'        => false,
    'current_run'     => null,
    'allowed_to_lock' => false,
    'reason'          => 'RFID not assigned'
  ]);
}

/* ---- On-track check via anti-join (no correlated subquery) ---- */
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
$stmt = $mysqli->prepare($sql_active);
if (!$stmt) error_out('Prepare failed', 500, ['detail'=>$mysqli->error]);
$stmt->bind_param('i', $snr);
$stmt->execute();
$res = $stmt->get_result();
$active = $res->fetch_assoc();
$stmt->close();

$on_track        = (bool)$active;
$current_run     = $active ? (int)$active['current_run'] : null;
$allowed_to_lock = !$on_track;

/* ---- Reply ---- */
respond('ok', [
  'participant'     => $participant,
  'rfid_uid_le'     => $rfid,
  'on_track'        => $on_track,
  'current_run'     => $current_run,
  'allowed_to_lock' => $allowed_to_lock,
  'reason'          => $on_track ? 'Racer is currently on track' : 'Allowed'
]);
