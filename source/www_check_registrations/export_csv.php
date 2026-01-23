<?php
// Turn on error reporting
error_reporting(E_ALL);
ini_set('display_errors', 1);

// Prevent any output before headers
ob_start();

require_once 'config.php';

// Check if user is logged in
if (!isset($_SESSION['user_id'])) {
    header('Location: index.php');
    exit();
}

// Clear any accidental output
ob_end_clean();

// Get database connection
$conn = getDBConnection();

// Set connection charset to UTF-8
$conn->set_charset('utf8');

// Fetch ALL data from participants table (no filters)
$sql = "SELECT * FROM participants ORDER BY Registrierungsnummer DESC";
$stmt = $conn->prepare($sql);
$stmt->execute();
$result = $stmt->get_result();

// Set headers FIRST - add UTF-8 BOM for Excel compatibility
header('Content-Type: text/csv; charset=utf-8');
header('Content-Disposition: attachment; filename="teilnehmer_registrierung_' . date('Y-m-d_H-i') . '.csv"');

// Create output buffer for BOM
ob_start();
echo "\xEF\xBB\xBF"; // UTF-8 BOM for Excel compatibility

// Output CSV
echo "Registrierungsnummer;Registriert am;Name;Vorname;Nickname;Telefon;E-mail;Kategorie;Geburtsdatum;Gewicht (kg)\r\n";

while ($row = $result->fetch_assoc()) {
    echo $row['Registrierungsnummer'] . ';' .
         date('d.m.Y H:i', strtotime($row['created_at'])) . ';' .
         str_replace(';', ',', $row['Name']) . ';' .
         str_replace(';', ',', $row['Vorname']) . ';' .
         ($row['Nickname'] ? str_replace(';', ',', $row['Nickname']) : '') . ';' .
         ($row['Phone'] ? str_replace(';', ',', $row['Phone']) : '') . ';' .
         ($row['E-mail'] ? str_replace(';', ',', $row['E-mail']) : '') . ';' .
         $row['Kategorie'] . ';' .
         (($row['Geburtsdatum'] && $row['Geburtsdatum'] != '2015-01-01') ? date('d.m.Y', strtotime($row['Geburtsdatum'])) : '') . ';' .
         ($row['Gewicht'] ?: '') . "\r\n";
}

// Flush output
ob_end_flush();

// Close connections
$stmt->close();
$conn->close();
exit();
?>