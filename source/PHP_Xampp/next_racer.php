<?php
// get_participant.php — returns JSON for one Startnummer

ini_set('display_errors', '0');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-cache, no-store, must-revalidate');

$servername = "localhost";
$username   = "root";   // adjust
$password   = "";       // adjust
$dbname     = "zeitmessung_V2";

// Check input
if (!isset($_GET['snr']) || !ctype_digit($_GET['snr'])) {
    http_response_code(400);
    echo json_encode(['error' => 'invalid_or_missing_Startnummer']);
    exit;
}

$snr = (int)$_GET['snr'];

try {
    $pdo = new PDO(
        "mysql:host=$servername;dbname=$dbname;charset=utf8mb4",
        $username,
        $password,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]
    );

    $stmt = $pdo->prepare("SELECT * FROM participant WHERE Startnummer = :snr");
    $stmt->bindValue(':snr', $snr, PDO::PARAM_INT);
    $stmt->execute();
    $row = $stmt->fetch();

    if ($row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE);
    } else {
        http_response_code(404);
        echo json_encode(['error' => 'not_found']);
    }

} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['error' => 'server_error']);
}

?>
