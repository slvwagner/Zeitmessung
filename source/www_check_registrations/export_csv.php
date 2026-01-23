<?php
require_once 'config.php';

// Check if user is logged in
if (!isset($_SESSION['user_id'])) {
    header('Location: index.php');
    exit();
}

// Get database connection
$conn = getDBConnection();

// Fetch ALL data from participants table (no filters)
$sql = "SELECT * FROM participants ORDER BY Registrierungsnummer DESC";
$stmt = $conn->prepare($sql);
$stmt->execute();
$result = $stmt->get_result();

// Set headers for CSV download
header('Content-Type: text/csv; charset=utf-8');
header('Content-Disposition: attachment; filename="teilnehmer_registrierung_' . date('Y-m-d_H-i') . '.csv"');

// Create output stream
$output = fopen('php://output', 'w');

// Add UTF-8 BOM for Excel compatibility
fputs($output, "\xEF\xBB\xBF");

// Write CSV headers
$headers = array(
    'Registrierungsnummer',
    'Registriert am',
    'Name',
    'Vorname',
    'Nickname',
    'Telefon',
    'E-mail',
    'Kategorie',
    'Geburtsdatum',
    'Gewicht (kg)'
);
fputcsv($output, $headers, ';');

// Write data rows
while ($row = $result->fetch_assoc()) {
    $data = array(
        $row['Registrierungsnummer'],
        date('d.m.Y H:i', strtotime($row['created_at'])),
        $row['Name'],
        $row['Vorname'],
        $row['Nickname'] ?: '',
        $row['Phone'] ?: '',
        $row['E-mail'] ?: '',
        $row['Kategorie'],
        ($row['Geburtsdatum'] && $row['Geburtsdatum'] != '2015-01-01') ? date('d.m.Y', strtotime($row['Geburtsdatum'])) : '',
        $row['Gewicht'] ?: ''
    );
    fputcsv($output, $data, ';');
}

// Close connections
$stmt->close();
$conn->close();
fclose($output);
exit();
?>