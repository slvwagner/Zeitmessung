<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { http_response_code(204); exit; }

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung_V2';

$REQUIRE_API_KEY = false;
$SERVER_API_KEY  = 'change_me';

function respond($status,$data=null,$http=200){ http_response_code($http); echo json_encode(['status'=>$status,'data'=>$data], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES); exit; }
function error_out($m,$h=400,$x=[]){ respond('error', array_merge(['message'=>$m],$x), $h); }

$raw = null; $body = null;
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' &&
    isset($_SERVER['CONTENT_TYPE']) &&
    stripos($_SERVER['CONTENT_TYPE'], 'application/json') !== false) {
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

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
$mysqli->set_charset('utf8mb4');

$sql = "SELECT 
          Startnummer, created_at, last_updated, race_order, last_run, next_run,
          Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum, Gewicht,
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

respond('ok', ['participant'=>$participant, 'rfid_uid_le'=>$rfid]);
