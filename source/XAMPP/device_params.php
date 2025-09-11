<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Access-Control-Allow-Methods: GET, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { http_response_code(204); exit; }

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung_V2';

$REQUIRE_API_KEY = false;
$SERVER_API_KEY  = 'change_me';

function respond($status,$data=null,$http=200){ http_response_code($http); echo json_encode(['status'=>$status,'data'=>$data], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES); exit; }
function error_out($m,$h=400,$x=[]){ respond('error', array_merge(['message'=>$m],$x), $h); }

if ($REQUIRE_API_KEY) {
  $hdr = $_SERVER['HTTP_X_API_KEY'] ?? '';
  if ($hdr !== $SERVER_API_KEY) error_out('bad api key', 401);
}

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
$mysqli->set_charset('utf8mb4');

$sql = "SELECT name, value FROM system_settings";
$res = $mysqli->query($sql);
if (!$res) error_out('query failed', 500, ['detail'=>$mysqli->error]);
$rows = $res->fetch_all(MYSQLI_ASSOC);
$res->close();

$out = [];
foreach ($rows as $r) {
  $k = strtolower(trim($r['name']));
  $v = trim($r['value']);
  // normalize a few known keys
  if ($k === 'relock_cooldown_s') $out['relock_cooldown_s'] = (int)$v;
  if ($k === 'min_start_interval_ms') $out['min_start_interval_ms'] = (int)$v;
  if ($k === 'uid_cooldown_ms') $out['uid_cooldown_ms'] = (int)$v;
  if ($k === 'track_headway_s')  $out['track_headway_s'] = (int)$v;
  if ($k === 'track_headway_ms') $out['track_headway_ms'] = (int)$v;

}

respond('success', $out);
