<?php
declare(strict_types=1);

// --- SSE headers ---
header('Content-Type: text/event-stream; charset=utf-8');
header('Cache-Control: no-cache');
header('Connection: keep-alive');
header('X-Accel-Buffering: no');     // disable nginx buffering if present
header('Access-Control-Allow-Origin: *');

// Try to turn off output buffering
while (ob_get_level() > 0) { ob_end_flush(); }
ob_implicit_flush(true);
ignore_user_abort(true);
set_time_limit(0);

// --- DB config ---
$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung';

// --- helpers ---
function fetch_rows(mysqli $db, int $limit): array {
  $sql = "
  SELECT o.Startnummer, o.run, o.started_at, p.Name, p.Vorname
  FROM (
    SELECT r1.Startnummer, r1.run,
           DATE_FORMAT(MIN(r1.timestamp_ms), '%Y-%m-%d %H:%i:%s') AS started_at
    FROM race r1
    WHERE r1.race_status = 'started'
    GROUP BY r1.Startnummer, r1.run
    HAVING NOT EXISTS (
      SELECT 1 FROM race rf
      WHERE rf.Startnummer = r1.Startnummer
        AND rf.run         = r1.run
        AND rf.race_status IN ('finished','time confirmed','disqualified')
    )
  ) AS o
  LEFT JOIN participant p ON p.Startnummer = o.Startnummer
  ORDER BY o.started_at ASC
  LIMIT ?
  ";
  $stmt = $db->prepare($sql);
  if (!$stmt) return [];
  $stmt->bind_param('i', $limit);
  $stmt->execute();
  $res  = $stmt->get_result();
  $rows = $res->fetch_all(MYSQLI_ASSOC);
  $stmt->close();
  return $rows ?: [];
}

function sse_send(string $event, $data): void {
  echo "event: {$event}\n";
  echo "data: " . json_encode($data, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES) . "\n\n";
}

function sse_ping(): void {
  echo ": keepalive " . time() . "\n\n";
}

// --- connect DB ---
$db = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($db->connect_errno) {
  sse_send('update', ['rows'=>[], 'error'=>'DB connect failed']);
  flush();
  exit;
}
$db->set_charset('utf8mb4');

// --- config ---
$limit     = isset($_GET['limit']) ? max(1, min(100, (int)$_GET['limit'])) : 50;
$interval  = 1;  // seconds
$last_hash = '';

while (!connection_aborted()) {
  $rows = fetch_rows($db, $limit);
  $hash = md5(json_encode($rows));

  if ($hash !== $last_hash) {
    sse_send('update', ['rows'=>$rows]);
    $last_hash = $hash;
  } else {
    sse_ping();
  }
  flush();
  sleep($interval);
}

// If we’re here, client disconnected
exit;
