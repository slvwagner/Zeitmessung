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
    die(json_encode(["status" => "error", "message" => "Connection failed: " . $conn->connect_error]));
}

// Get JSON input
$json = file_get_contents('php://input');
$data = json_decode($json, true);

// Validate input
if (json_last_error() !== JSON_ERROR_NONE) {
    die(json_encode(["status" => "error", "message" => "Invalid JSON data"]));
}

$value = $data['value'] ?? '';
$timestamp = $data['timestamp'] ?? date("Y-m-d H:i:s.v");

// Debug logging (optional)
file_put_contents('pico_debug.log', 
    "[" . date("Y-m-d H:i:s") . "] Received: " . print_r($data, true) . "\n", 
    FILE_APPEND);

// Prepare SQL statement
$sql = "INSERT INTO my_table (value, created_at) VALUES (?, ?)";
$stmt = $conn->prepare($sql);

if (!$stmt) {
    die(json_encode(["status" => "error", "message" => "Prepare failed: " . $conn->error]));
}

// Bind parameters (using DATETIME(3) for millisecond precision)
$stmt->bind_param("ss", $value, $timestamp);

// Execute and respond
if ($stmt->execute()) {
    echo json_encode(["status" => "success", "message" => "Data inserted"]);
} else {
    echo json_encode(["status" => "error", "message" => "Execute failed: " . $stmt->error]);
}

$stmt->close();
$conn->close();
?>