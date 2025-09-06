<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');

$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung_V2';

function respond($status,$data=null,$http=200){
  http_response_code($http);
  echo json_encode(['status'=>$status,'data'=>$data],
    JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
  exit;
}
function error_out($m,$h=400,$x=[]){
  respond('error', array_merge(['message'=>$m],$x), $h);
}

$limit = isset($_GET['limit']) ? max(1, min(200, (int)$_GET['limit'])) : 200;

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
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
