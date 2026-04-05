// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.2';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
// edit.php — update a single race row field (whitelisted)
header('Content-Type: application/json; charset=UTF-8');

// === DB CONFIG ===
$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung';
$DB_USER = 'root';
$DB_PASS = '';

try {
  $pdo = new PDO(
    "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
    $DB_USER,
    $DB_PASS,
    [
      PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
      PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]
  );
} catch (PDOException $e) {
  http_response_code(500);
  echo json_encode(["status"=>"error","message"=>"DB connection failed: ".$e->getMessage()]);
  exit;
}

$raw = file_get_contents('php://input');
$data = json_decode($raw, true);
if (!is_array($data)) {
  http_response_code(400);
  echo json_encode(["status"=>"error","message"=>"Invalid JSON"]);
  exit;
}

$id    = isset($data['id']) ? (int)$data['id'] : 0;
$field = isset($data['field']) ? (string)$data['field'] : '';
$value = $data['new_value'] ?? null;

if ($id <= 0 || $field === '') {
  http_response_code(400);
  echo json_encode(["status"=>"error","message"=>"id and field required"]);
  exit;
}

// Whitelist + length caps
$allowed = [
  'race_status'  => 50,
  'device_name'  => 50,
  'device_id'    => 32,
  'run'          => 'int',
  'timestamp_ms' => 'datetime_ms',
];

if (!array_key_exists($field, $allowed)) {
  http_response_code(400);
  echo json_encode(["status"=>"error","message"=>"Field not allowed"]);
  exit;
}

// Normalize based on type
if ($allowed[$field] === 'int') {
  $value = (int)$value;
} elseif ($allowed[$field] === 'datetime_ms') {
  // Accept epoch ms or ISO8601
  $ts_mysql = null;
  if ($value !== null && $value !== '') {
    if (preg_match('/^\d{12,}$/', (string)$value)) {
      $ms  = (int)$value; $sec = floor($ms/1000); $frac = $ms % 1000;
      $dt = DateTime::createFromFormat('U.u', sprintf("%d.%03d", $sec, $frac));
      if ($dt) { $dt->setTimezone(new DateTimeZone('UTC')); $ts_mysql = $dt->format('Y-m-d H:i:s.u'); }
    } else {
      try { $dt = new DateTime((string)$value); $dt->setTimezone(new DateTimeZone('UTC')); $ts_mysql = $dt->format('Y-m-d H:i:s.u'); }
      catch (Exception $e) { $ts_mysql = null; }
    }
  }
  $value = $ts_mysql; // can be NULL
} else {
  $max = (int)$allowed[$field];
  $value = substr((string)$value, 0, $max);
}

try {
  $sql = "UPDATE race SET `$field` = :v WHERE id = :id";
  $stmt = $pdo->prepare($sql);
  $stmt->execute([':v' => $value, ':id' => $id]);
  echo json_encode(["status"=>"success","affected_rows"=>$stmt->rowCount()]);
} catch (PDOException $e) {
  http_response_code(500);
  echo json_encode(["status"=>"error","message"=>$e->getMessage()]);
}
?>



