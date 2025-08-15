<?php
header('Content-Type: application/json');

$servername = "localhost";
$username = "root";
$password = "";
$dbname = "zeitmessung";

// Create connection
$conn = new mysqli($servername, $username, $password, $dbname);

// Check connection
if ($conn->connect_error) {
    die(json_encode([
        "status" => "error",
        "message" => "Connection failed: " . $conn->connect_error
    ]));
}

// Get JSON input
$json = file_get_contents('php://input');
$data = json_decode($json, true);

// Validate input
if (json_last_error() !== JSON_ERROR_NONE) {
    die(json_encode([
        "status" => "error",
        "message" => "Invalid JSON data"
    ]));
}

$value        = $data['value'] ?? '';
$timestamp    = $data['timestamp'] ?? date("Y-m-d H:i:s.v");
$device_id    = $data['device_id'] ?? 'unknown';
$device_name  = $data['device_name'] ?? 'unnamed';
$race_status  = $data['race_status'] ?? 'unknown';

// Debug logging (optional)
file_put_contents(
    'Zeitmessung_write_debug.log', 
    "[" . date("Y-m-d H:i:s") . "] Received: " . print_r($data, true) . "\n", 
    FILE_APPEND
);

// Prepare SQL statement (now with device_id + device_name)
$sql = "INSERT INTO race (value, timestamp, device_id, device_name, race_status) VALUES (?, ?, ?, ?, ?)";
$stmt = $conn->prepare($sql);

if (!$stmt) {
    die(json_encode([
        "status" => "error",
        "message" => "Prepare failed: " . $conn->error
    ]));
}

// Bind parameters
$stmt->bind_param("sssss", $value, $timestamp, $device_id, $device_name, $race_status);

// Execute and respond
if ($stmt->execute()) {
    echo json_encode([
        "status" => "success",
        "message" => "Data inserted"
    ]);
} else {
    echo json_encode([
        "status" => "error",
        "message" => "Execute failed: " . $stmt->error
    ]);
}

$stmt->close();
$conn->close();
?>
