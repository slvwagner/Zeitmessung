// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.2';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
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
$DB_NAME = 'zeitmessung';

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

$sql = "SELECT name, value, unit FROM system_settings";
$res = $mysqli->query($sql);
if (!$res) error_out('query failed', 500, ['detail'=>$mysqli->error]);
$rows = $res->fetch_all(MYSQLI_ASSOC);
$res->close();

$out = [];
foreach ($rows as $r) {
  $k = strtolower(trim($r['name']));
  $v = trim($r['value']);
  $unit = isset($r['unit']) ? trim($r['unit']) : null;
  
  // Convert values to appropriate types based on unit
  switch ($k) {
    case 'relock cooldown time':
      $out['relock_cooldown_s'] = (int)$v;
      break;
    case 'track_headway time':
      $out['track_headway_s'] = (int)$v;
      break;
    case 'beam distance':
      $out['beam_distance_mm'] = (float)$v;
      break;
    case 'beam pair timeout':
      // Convert ms to seconds if unit is ms
      if ($unit === 'ms') {
        $out['beam_pair_timeout_ms'] = (int)$v;
        $out['beam_pair_timeout_s'] = (int)$v / 1000;
      } else {
        $out['beam_pair_timeout_s'] = (int)$v;
      }
      break;
    case 'local_time_offset':
      $out['local_time_offset_h'] = (int)$v;
      break;
    default:
      // For any other settings, store with original name
      $out[str_replace(' ', '_', $k)] = is_numeric($v) ? (float)$v : $v;
  }
}

// Add unit information if needed
foreach ($rows as $r) {
  $k = strtolower(trim($r['name']));
  $unit = isset($r['unit']) ? trim($r['unit']) : null;
  
  if ($unit) {
    $key = str_replace(' ', '_', $k) . '_unit';
    $out[$key] = $unit;
  }
}

respond('success', $out);
?>



