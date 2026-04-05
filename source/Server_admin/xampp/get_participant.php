// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.2';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
// get_participant.php — fetch participant by Startnummer or list latest
header('Content-Type: application/json; charset=UTF-8');

$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung';
$DB_USER = 'root';
$DB_PASS = '';

try {
  $pdo = new PDO(
    "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
    $DB_USER,
    $DB_PASS,
    [ PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
      PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC ]
  );
} catch (PDOException $e) {
  http_response_code(500);
  echo json_encode(["status"=>"error","message"=>"DB connection failed: ".$e->getMessage()]);
  exit;
}

$sn = isset($_GET['Startnummer']) ? (int)$_GET['Startnummer'] : null;

try {
  if ($sn) {
    $stmt = $pdo->prepare("SELECT * FROM participant WHERE Startnummer = ?");
    $stmt->execute([$sn]);
    $row = $stmt->fetch();
    echo json_encode(["status"=>"success","data"=>$row]);
  } else {
    $stmt = $pdo->query("SELECT * FROM participant ORDER BY Startnummer DESC LIMIT 20");
    echo json_encode(["status"=>"success","data"=>$stmt->fetchAll()]);
  }
} catch (PDOException $e) {
  http_response_code(500);
  echo json_encode(["status"=>"error","message"=>$e->getMessage()]);
}
?>



