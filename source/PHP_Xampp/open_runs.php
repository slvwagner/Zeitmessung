<?php
// open_runs.php — list oldest "started but not finished" runs
declare(strict_types=1);
header('Content-Type: application/json; charset=UTF-8');

$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung_V2';
$DB_USER = 'root';
$DB_PASS = '';

$limit = isset($_GET['limit']) ? max(1, min(50, (int)$_GET['limit'])) : 8;

try {
  $pdo = new PDO("mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
                 $DB_USER, $DB_PASS, [
                   PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                   PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                 ]);
} catch (PDOException $e) {
  http_response_code(500);
  echo json_encode(["status"=>"error","message"=>"DB connect failed"]);
  exit;
}

/*
Find runs with a START but no FINISH:
- We take the earliest START per (Startnummer, run), then check if there is any FINISH for same pair.
*/
$sql = "
SELECT  r1.Startnummer, r1.run,
        MIN(r1.timestamp_ms) AS started_at
FROM    race r1
WHERE   r1.race_status = 'started'
GROUP BY r1.Startnummer, r1.run
HAVING  NOT EXISTS (
          SELECT 1 FROM race rf
          WHERE  rf.Startnummer = r1.Startnummer
            AND  rf.run         = r1.run
            AND  rf.race_status = 'finished'
        )
ORDER BY started_at ASC
LIMIT :lim
";
$stmt = $pdo->prepare($sql);
$stmt->bindValue(':lim', $limit, PDO::PARAM_INT);
$stmt->execute();
$rows = $stmt->fetchAll();

echo json_encode(["status"=>"success","data"=>$rows], JSON_UNESCAPED_UNICODE);
