<?php
// insert.php — insert a row into zeitmessung_V2.race
header('Content-Type: application/json; charset=UTF-8');

// === DB CONFIG ===
$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung_V2';
$DB_USER = 'root';    // change if needed
$DB_PASS = '';        // change if needed

try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER,
        $DB_PASS,
        [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        "status" => "error",
        "message" => "DB connection failed: " . $e->getMessage()
    ]);
    exit;
}

// === Read JSON payload ===
$raw = file_get_contents("php://input");
$data = json_decode($raw, true);

if (!is_array($data)) {
    http_response_code(400);
    echo json_encode([
        "status" => "error",
        "message" => "Invalid JSON"
    ]);
    exit;
}

// === Extract + validate ===
$Startnummer = isset($data['Startnummer']) ? (int)$data['Startnummer'] : 0;
$run         = isset($data['run']) ? (int)$data['run'] : 1;
$timestamp   = $data['timestamp_ms'] ?? null;
$timezone_offset = isset($data['timezone_offset']) ? (int)$data['timezone_offset'] : 0;
$device_id   = substr((string)($data['device_id'] ?? ''), 0, 32);
$device_name = substr((string)($data['device_name'] ?? ''), 0, 50);
$race_status = substr((string)($data['race_status'] ?? ''), 0, 50);

if ($Startnummer <= 0) {
    http_response_code(400);
    echo json_encode([
        "status" => "error",
        "message" => "Startnummer must be provided and > 0"
    ]);
    exit;
}
if ($device_id === '' || $device_name === '' || $race_status === '') {
    http_response_code(400);
    echo json_encode([
        "status" => "error",
        "message" => "device_id, device_name and race_status are required"
    ]);
    exit;
}

// === Normalize timestamp with timezone offset ===
$timestamp_mysql = null;
if ($timestamp !== null && $timestamp !== '') {
    if (preg_match('/^\d{12,}$/', (string)$timestamp)) {
        // Handle millisecond timestamp
        $ms  = (int)$timestamp;
        $sec = floor($ms / 1000);
        $frac = $ms % 1000;
        $dt = DateTime::createFromFormat('U.u', sprintf("%d.%03d", $sec, $frac));
        if ($dt) {
            // Apply timezone offset if provided
            if ($timezone_offset != 0) {
                $timezone_name = timezone_name_from_abbr("", $timezone_offset * 3600, false);
                if ($timezone_name) {
                    $dt->setTimezone(new DateTimeZone($timezone_name));
                }
            }
            $dt->setTimezone(new DateTimeZone('UTC'));
            $timestamp_mysql = $dt->format('Y-m-d H:i:s.u');
        }
    } else {
        try {
            // Handle string timestamp
            if ($timezone_offset != 0) {
                // Create timezone from offset
                $timezone_name = timezone_name_from_abbr("", $timezone_offset * 3600, false);
                if ($timezone_name) {
                    $dt = new DateTime($timestamp, new DateTimeZone($timezone_name));
                } else {
                    // Fallback: assume UTC and adjust manually
                    $dt = new DateTime($timestamp, new DateTimeZone('UTC'));
                    $dt->modify("$timezone_offset hours");
                }
            } else {
                // No timezone offset provided, assume UTC
                $dt = new DateTime($timestamp, new DateTimeZone('UTC'));
            }
            
            // Convert to UTC for storage
            $dt->setTimezone(new DateTimeZone('UTC'));
            $timestamp_mysql = $dt->format('Y-m-d H:i:s.u');
            
        } catch (Exception $e) {
            $timestamp_mysql = null;
            error_log("DateTime creation failed: " . $e->getMessage());
        }
    }
}

// === Ensure participant exists ===
$check = $pdo->prepare("SELECT 1 FROM participant WHERE Startnummer = ?");
$check->execute([$Startnummer]);
if (!$check->fetch()) {
    http_response_code(404);
    echo json_encode([
        "status" => "error",
        "message" => "Participant not found",
        "Startnummer" => $Startnummer
    ]);
    exit;
}

// === Insert into race ===
try {
    if ($timestamp_mysql === null) {
        $sql = "INSERT INTO race (Startnummer, run, timestamp_ms, device_id, device_name, race_status, timezone_offset)
                VALUES (?, ?, NULL, ?, ?, ?, ?)";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([$Startnummer, $run, $device_id, $device_name, $race_status, $timezone_offset]);
    } else {
        $sql = "INSERT INTO race (Startnummer, run, timestamp_ms, device_id, device_name, race_status, timezone_offset)
                VALUES (?, ?, ?, ?, ?, ?, ?)";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([$Startnummer, $run, $timestamp_mysql, $device_id, $device_name, $race_status, $timezone_offset]);
    }

    echo json_encode([
        "status" => "success",
        "id" => (int)$pdo->lastInsertId(),
        "Startnummer" => $Startnummer,
        "run" => $run,
        "timestamp_ms" => $timestamp_mysql,
        "timezone_offset" => $timezone_offset,
        "device_id" => $device_id,
        "device_name" => $device_name,
        "race_status" => $race_status
    ]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        "status" => "error",
        "message" => "Insert failed: " . $e->getMessage()
    ]);
}
