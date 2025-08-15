<?php
header('Content-Type: application/json');
// Force JSON response even on errors
ini_set('display_errors', 0);

$servername = "localhost";
$username = "root";
$password = "";
$dbname = "zeitmessung";

// Create connection
$conn = new mysqli($servername, $username, $password, $dbname);

// Check connection
if ($conn->connect_error) {
    http_response_code(500);
    die(json_encode([
        "status" => "error",
        "message" => "Connection failed",
        "details" => $conn->connect_error
    ]));
}

try {
    // Get JSON input
    $json = file_get_contents('php://input');
    if (empty($json)) {
        throw new Exception("No input data received");
    }
    
    $data = json_decode($json, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception("Invalid JSON input");
    }

    // Validate required fields
    $required = ['id', 'field', 'new_value'];
    foreach ($required as $field) {
        if (!isset($data[$field])) {
            throw new Exception("Missing required field: $field");
        }
    }

    // Sanitize input
    $id = (int)$data['id'];
    $field = preg_replace('/[^a-zA-Z0-9_]/', '', $data['field']);
    $new_value = $conn->real_escape_string($data['new_value']);

    // Validate field name
    $allowed_fields = ['value', 'timestamp', 'device_id', 'device_name', 'race_status'];
    if (!in_array($field, $allowed_fields)) {
        throw new Exception("Invalid field name: $field");
    }

    // Prepare and execute query
    $sql = "UPDATE race SET `$field` = '$new_value' WHERE id = $id";
    if (!$conn->query($sql)) {
        throw new Exception("Database update failed");
    }

    // Success response
    echo json_encode([
        "status" => "success",
        "message" => "Record updated",
        "affected_rows" => $conn->affected_rows,
        "id" => $id,
        "field" => $field,
        "new_value" => $new_value
    ]);

} catch (Exception $e) {
    http_response_code(400);
    echo json_encode([
        "status" => "error",
        "message" => $e->getMessage(),
        "input" => $json ?? null
    ]);
}

$conn->close();
?>
