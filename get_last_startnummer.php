<?php
header('Content-Type: application/json');

$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung";

$conn = new mysqli($servername, $username, $password, $dbname);

if ($conn->connect_error) {
    die(json_encode([
        "status"  => "error",
        "message" => $conn->connect_error
    ]));
}

// Query: latest record by highest id
$sql = "SELECT id, value, timestamp, device_id, device_name, race_status 
        FROM race 
        ORDER BY id DESC 
        LIMIT 1";

$result = $conn->query($sql);

$data = [];
if ($result && $row = $result->fetch_assoc()) {
    $data = $row;
}

// Extract startnummer (assuming "value" field looks like "Startnummer: 12")
$last_startnummer = 0;
if (!empty($data)) {
    if (preg_match('/(\d+)$/', $data['value'], $matches)) {
        $last_startnummer = intval($matches[1]);
    }
}

echo json_encode([
    "status"           => "success",
    "last_startnummer" => $last_startnummer,
    "data"             => $data
]);

$conn->close();
?>
