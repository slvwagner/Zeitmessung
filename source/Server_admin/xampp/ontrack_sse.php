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

function respond($status,$data=null,$http=200){ http_response_code($http); echo json_encode(['status'=>$status,'data'=>$data], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES); exit; }
function error_out($m,$h=400,$x=[]){ respond('error', array_merge(['message'=>$m],$x), $h); }

$limit = isset($_GET['limit']) ? max(1, min(100, (int)$_GET['limit'])) : 50;

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
$mysqli->set_charset('utf8mb4');

/*
Open runs = has a 'started' event and NO terminal event ('finished','time confirmed','disqualified')
Include participant name fields; order by oldest start first.
Get the speed_kmh from the started event.
*/
$sql = "
SELECT 
    o.Startnummer, 
    o.run, 
    o.started_at, 
    p.Name, 
    p.Vorname,
    o.speed_kmh
FROM (
  SELECT 
    r1.Startnummer, 
    r1.run,
    DATE_FORMAT(MIN(r1.timestamp_ms), '%Y-%m-%d %H:%i:%s') AS started_at,
    -- Get the speed_kmh from the earliest started event
    (SELECT r2.speed_kmh 
     FROM race r2 
     WHERE r2.Startnummer = r1.Startnummer 
       AND r2.run = r1.run 
       AND r2.race_status = 'started'
     ORDER BY r2.timestamp_ms ASC 
     LIMIT 1) AS speed_kmh
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

$stmt = $mysqli->prepare($sql);
if (!$stmt) error_out('Prepare failed', 500, ['detail'=>$mysqli->error]);
$stmt->bind_param('i', $limit);
$stmt->execute();
$res  = $stmt->get_result();
$rows = $res->fetch_all(MYSQLI_ASSOC);
$stmt->close();

respond('success', $rows);
?>