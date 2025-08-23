<?php
// Database connection settings
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

try {
    // Connect to database using PDO
    $pdo = new PDO("mysql:host=$servername;dbname=$dbname;charset=utf8mb4", $username, $password);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Query participant table ordered by race_order
    $stmt = $pdo->query("SELECT * FROM participant ORDER BY race_order ASC");

    // Fetch all rows
    $participants = $stmt->fetchAll(PDO::FETCH_ASSOC);

    // Display results
    if ($participants) {
        echo "<table border='1' cellpadding='5' cellspacing='0'>";
        echo "<tr style='background:#eee;'>";
        foreach (array_keys($participants[0]) as $colName) {
            echo "<th>" . htmlspecialchars($colName) . "</th>";
        }
        echo "</tr>";

        foreach ($participants as $row) {
            echo "<tr>";
            foreach ($row as $value) {
                echo "<td>" . htmlspecialchars($value) . "</td>";
            }
            echo "</tr>";
        }
        echo "</table>";
    } else {
        echo "No participants found.";
    }

} catch (PDOException $e) {
    echo "❌ Database error: " . $e->getMessage();
}
?>
