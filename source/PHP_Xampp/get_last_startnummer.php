<?php
header('Content-Type: application/json');

$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung";

$conn = new mysqli($servername, $username, $password, $dbname);
$conn->set_charset('utf8mb4');

if ($conn->connect_error) {
    http_response_code(500);
    echo json_encode(["status"=>"error","message"=>$conn->connect_error]);
    exit;
}

/*
  GOAL: last_startnummer = highest numeric value found in strings like "Startnummer: 15"
  Prefer MySQL 8 (REGEXP_SUBSTR). Fallback to MySQL 5.7-compatible expression.
*/

// First try MySQL 8 approach:
$sqlMax8 = "
  SELECT
    MAX(CAST(REGEXP_SUBSTR(value, '[0-9]+') AS UNSIGNED)) AS last_startnummer
  FROM race
  WHERE value LIKE 'Startnummer:%'
";
$res = $conn->query($sqlMax8);

if ($res === false) {
    // Fallback for MySQL 5.7: assume the format is exactly "Startnummer: <digits>" (no trailing text)
    $sqlMax57 = "
      SELECT
        MAX(CAST(TRIM(SUBSTRING_INDEX(value, ':', -1)) AS UNSIGNED)) AS last_startnummer
      FROM race
      WHERE value LIKE 'Startnummer:%'
    ";
    $res = $conn->query($sqlMax57);
    if ($res === false) {
        http_response_code(500);
        echo json_encode(["status"=>"error","message"=>$conn->error]);
        exit;
    }
}

$row = $res->fetch_assoc();
$last = isset($row['last_startnummer']) ? (int)$row['last_startnummer'] : null;

// Optional: also return the newest row that has that Startnummer (for context only)
$data = null;
if ($last !== null) {
    $stmt = $conn->prepare("
        SELECT id, value, timestamp, device_id, device_name, race_status
        FROM race
        WHERE value REGEXP CONCAT('^Startnummer:[[:space:]]*', ? , '$')
        ORDER BY id DESC
        LIMIT 1
    ");
    $stmt->bind_param('i', $last);
    if ($stmt->execute()) {
        $r = $stmt->get_result();
        if ($r && $r->num_rows) $data = $r->fetch_assoc();
    }
    $stmt->close();
}

echo json_encode([
    "status"           => "success",
    "last_startnummer" => $last,   // <- this will be 15 for your table
    "data"             => $data    // optional context row; remove if not needed
], JSON_UNESCAPED_UNICODE);

$conn->close();
