<?php
// Database connection settings
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

header('Content-Type: application/json; charset=utf-8');

try {
    // Connect to database using PDO
    $pdo = new PDO("mysql:host=$servername;dbname=$dbname;charset=utf8mb4", $username, $password);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Query participant table ordered by race_order
    $stmt = $pdo->query("SELECT * FROM participant ORDER BY Startnummer ASC");

    // Fetch all rows as associative array
    $participants = $stmt->fetchAll(PDO::FETCH_ASSOC);

    // Return JSON response
    echo json_encode($participants, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);

} catch (PDOException $e) {
    // Return error in JSON format
    echo json_encode([
        "error" => true,
        "message" => $e->getMessage()
    ]);
}
?>

