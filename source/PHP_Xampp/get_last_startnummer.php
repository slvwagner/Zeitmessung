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
  We want the latest *start* record, not the latest *finish* record.
  Adjust the list below if your start statuses differ.
*/
$sql = "
  SELECT id, value, timestamp, device_id, device_name, race_status
  FROM race
  WHERE device_name = 'Start'
    AND race_status IN ('race_started','started_and_finished')
    AND value LIKE 'Startnummer:%'
  ORDER BY id DESC
  LIMIT 1
";

$result = $conn->query($sql);
if ($result === false) {
    http_response_code(500);
    echo json_encode(["status"=>"error","message"=>$conn->error]);
    exit;
}

$data = $result->fetch_assoc() ?: [];

$last_startnummer = null;
if (!empty($data) && isset($data['value'])) {
    if (preg_match('/\bStartnummer:\s*(\d+)\b/u', $data['value'], $m)) {
        $last_startnummer = (int)$m[1];
    }
}

echo json_encode([
    "status"           => "success",
    "last_startnummer" => $last_startnummer,
    "data"             => $data
], JSON_UNESCAPED_UNICODE);

$conn->close();
