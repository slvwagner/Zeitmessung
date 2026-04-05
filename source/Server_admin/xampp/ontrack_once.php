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

function respond($status,$data=null,$http=200){ http_response_code($http); echo json_encode(['status'=>$status,'data'=>$data], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES); exit; }
function error_out($m,$h=400,$x=[]){ respond('error', array_merge(['message'=>$m],$x), $h); }

$limit = isset($_GET['limit']) ? max(1, min(100, (int)$_GET['limit'])) : 50;

$mysqli = @new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if ($mysqli->connect_errno) error_out('DB connect failed', 500, ['detail'=>$mysqli->connect_error]);
$mysqli->set_charset('utf8mb4');

/*
Simpler approach: Get active racers and their latest speed in one query
*/
$sql = "
SELECT 
    active.Startnummer,
    active.run,
    active.started_at,
    p.Name,
    p.Vorname,
    (
        SELECT r2.speed_kmh 
        FROM race r2 
        WHERE r2.Startnummer = active.Startnummer 
            AND r2.run = active.run 
            AND r2.speed_kmh IS NOT NULL
        ORDER BY r2.timestamp_ms DESC 
        LIMIT 1
    ) as speed_kmh
FROM (
    SELECT 
        r1.Startnummer,
        r1.run,
        MIN(r1.timestamp_ms) as started_at
    FROM race r1
    WHERE r1.race_status = 'started'
    GROUP BY r1.Startnummer, r1.run
    HAVING NOT EXISTS (
        SELECT 1 
        FROM race rf 
        WHERE rf.Startnummer = r1.Startnummer
            AND rf.run = r1.run
            AND rf.race_status IN ('finished','time confirmed','disqualified')
    )
) AS active
LEFT JOIN participant p ON p.Startnummer = active.Startnummer
ORDER BY active.started_at ASC
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



