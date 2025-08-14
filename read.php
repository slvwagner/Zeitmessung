<?php
header('Content-Type: application/json');

$servername = "localhost";
$username = "root";
$password = "";
$dbname = "zeitmessung";

$conn = new mysqli($servername, $username, $password, $dbname);

if ($conn->connect_error) {
    die(json_encode([
        "status" => "error",
        "message" => $conn->connect_error
    ]));
}

// Optional device_id filter from URL
$filter_device_id = isset($_GET['device_id']) ? $_GET['device_id'] : null;

// Build SQL with optional WHERE
if ($filter_device_id) {
    $sql = "SELECT value, timestamp, device_id, device_name, race_status 
            FROM race 
            WHERE device_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 10";
    $stmt = $conn->prepare($sql);
    $stmt->bind_param("s", $filter_device_id);
    $stmt->execute();
    $result = $stmt->get_result();
} else {
    $sql = "SELECT value, timestamp, device_id, device_name, race_status 
            FROM race 
            ORDER BY timestamp DESC 
            LIMIT 10";
    $result = $conn->query($sql);
}

$data = [];
if ($result) {
    while ($row = $result->fetch_assoc()) {
        $data[] = $row;
    }
}

echo json_encode([
    "status" => "success",
    "data" => $data
]);

$conn->close();
?>
