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

// Get optional filters from URL
$filter_id = isset($_GET['id']) ? $_GET['id'] : null;
$filter_race_status = isset($_GET['race_status']) ? $_GET['race_status'] : null;

// Build SQL based on filters
$sql = "SELECT id, value, timestamp, device_id, device_name, race_status FROM race";
$where = [];
$params = [];
$types = "";

if ($filter_id) {
    $where[] = "id = ?";
    $params[] = $filter_id;
    $types .= "s";
}

if ($filter_race_status) {
    $where[] = "race_status = ?";
    $params[] = $filter_race_status;
    $types .= "s";
}

if (!empty($where)) {
    $sql .= " WHERE " . implode(" AND ", $where);
}

// Changed from DESC to ASC to get oldest first
$sql .= " ORDER BY timestamp ASC";

// Prepare and execute query
if (!empty($params)) {
    $stmt = $conn->prepare($sql);
    $stmt->bind_param($types, ...$params);
    $stmt->execute();
    $result = $stmt->get_result();
} else {
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
