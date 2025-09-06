<?php
declare(strict_types=1);

// SSE headers
header('Content-Type: text/event-stream');
header('Cache-Control: no-cache');
header('Connection: keep-alive');
header('Access-Control-Allow-Origin: *');

@ini_set('output_buffering', 'off');
@ini_set('zlib.output_compression', '0');
@ini_set('implicit_flush', '1');
@apache_setenv('no-gzip', '1');
while (ob_get_level() > 0) { ob_end_flush(); }
ob_implicit_flush(true);

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung_V2';

// How often to re-check DB (seconds)
$INTERVAL = 1;
// Optional: cap the stream duration (seconds). 0 = unlimited.
$MAX_SECONDS = 0;

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) {
  echo "event: error\n";
  echo 'data: '.json_encode(['message'=>'DB connect failed'])."\n\n";
  flush();
  exit;
}
$mysqli->set_charset('utf8mb4');

$sql = "
SELECT  r1.Startnummer, p.Name, p.Vorname, r1.run,
        MIN(r1.timestamp_ms) AS started_at
FROM    race r1
JOIN    participant p USING (Startnummer)
WHERE   r1.race_status = 'started'
GROUP BY r1.Startnummer, r1.run, p.Name, p.Vorname
HAVING  NOT EXISTS (
          SELECT 1
          FROM   race rf
          WHERE  rf.Startnummer = r1.Startnummer
            AND  rf.run         = r1.run
            AND  rf.race_status IN ('finished','time confirmed')
        )
ORDER BY started_at ASC
";

$lastHash = null;
$startedAt = time();

function now_data(mysqli $db, string $sql): array {
  $res = $db->query($sql);
  if (!$res) return [];
  $rows = $res->fetch_all(MYSQLI_ASSOC);
  $res->free();
  return $rows ?: [];
}

function push_event(string $event, $data, ?string $id=null): void {
  if ($id !== null) echo "id: $id\n";
  echo "event: $event\n";
  echo 'data: '.json_encode($data, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n\n";
  flush();
}

while (true) {
  if ($MAX_SECONDS > 0 && (time() - $startedAt) >= $MAX_SECONDS) {
    push_event('end', ['message'=>'stream closed by server']);
    break;
  }

  $rows = now_data($mysqli, $sql);

  // Build a stable hash of the set: changes in list/order trigger update
  $payloadForHash = array_map(fn($r)=>[
    (int)$r['Startnummer'],
    (int)$r['run'],
    (string)$r['Name'],
    (string)$r['Vorname'],
    (string)$r['started_at']
  ], $rows);

  $hash = hash('sha256', json_encode($payloadForHash));
  if ($hash !== $lastHash) {
    $lastHash = $hash;
    push_event('update', [
      'hash'   => $hash,
      'count'  => count($rows),
      'rows'   => $rows
    ], $hash);
  } else {
    // heartbeat keeps connection alive
    echo ": ping\n\n";
    flush();
  }

  // Sleep a bit before re-checking
  sleep($INTERVAL);
}
