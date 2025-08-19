<?php
// api/race_update.php
header('Content-Type: application/json');
ini_set('display_errors', 0);

// --- DB CONFIG ---
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

// --- MYSQLI EXCEPTIONS & CONNECT ---
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
try {
    $conn = new mysqli($servername, $username, $password, $dbname);
    $conn->set_charset('utf8mb4');
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode([
        "status"  => "error",
        "message" => "Connection failed",
        "details" => $e->getMessage()
    ]);
    exit;
}

try {
    // --- READ JSON BODY ---
    $json = file_get_contents('php://input');
    if ($json === '' || $json === false) {
        throw new Exception("No input data received");
    }
    $data = json_decode($json, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception("Invalid JSON input");
    }

    // --- REQUIRED KEYS ---
    foreach (['id','field','new_value'] as $k) {
        if (!array_key_exists($k, $data)) {
            throw new Exception("Missing required field: $k");
        }
    }

    // --- INPUTS ---
    $id    = (int)$data['id'];
    $field = (string)$data['field'];
    // allow null explicitly (to set column to NULL), otherwise take given value
    $newValueProvided = array_key_exists('new_value', $data);
    $newValue = $newValueProvided ? $data['new_value'] : null;

    if ($id <= 0) {
        throw new Exception("Invalid id");
    }

    // --- WHITELIST & TYPE MAP ---
    // map client field -> [column_name, mysqli_bind_type]
    $allowed = [
        'participant_id' => ['participant_id', 'i'],
        'run'            => ['run', 'i'],
        'timestamp_ms'   => ['timestamp_ms', 's'], // DATETIME(3) string or NULL
        'device_id'      => ['device_id', 's'],
        'device_name'    => ['device_name', 's'],
        'race_status'    => ['race_status', 's'],
    ];

    if (!isset($allowed[$field])) {
        throw new Exception("Invalid field name: $field");
    }

    [$column, $type] = $allowed[$field];

    // --- OPTIONAL VALIDATION BY FIELD ---
    if ($field === 'timestamp_ms') {
        // Accepts 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', or with milliseconds '...SS.mmm'
        // Or allow NULL to clear the value
        if ($newValue !== null && $newValue !== '') {
            $pattern = '/^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)?$/';
            if (!preg_match($pattern, $newValue)) {
                throw new Exception("timestamp_ms must be 'YYYY-MM-DD[ HH:MM:SS[.mmm]]' or null");
            }
        } else {
            // treat empty string as NULL to clear the column
            $newValue = null;
        }
    } elseif ($field === 'participant_id' || $field === 'run') {
        if ($newValue === null || !is_numeric($newValue)) {
            throw new Exception("$field must be an integer");
        }
        $newValue = (int)$newValue;
        if ($newValue < 0) {
            throw new Exception("$field cannot be negative");
        }
    } else {
        // device_id, device_name, race_status
        if ($newValue === null) {
            throw new Exception("$field cannot be null");
        }
        $newValue = (string)$newValue;
        // quick length guards
        if ($field === 'device_id' && strlen($newValue) > 32) {
            throw new Exception("device_id too long (max 32)");
        }
        if ($field === 'device_name' && strlen($newValue) > 50) {
            throw new Exception("device_name too long (max 50)");
        }
        if ($field === 'race_status' && strlen($newValue) > 50) {
            throw new Exception("race_status too long (max 50)");
        }
    }

    // --- BUILD & EXECUTE UPDATE ---
    if ($newValue === null) {
        // Set column to NULL explicitly
        $sql  = "UPDATE race SET `$column` = NULL WHERE id = ?";
        $stmt = $conn->prepare($sql);
        $stmt->bind_param('i', $id);
    } else {
        $sql  = "UPDATE race SET `$column` = ? WHERE id = ?";
        $stmt = $conn->prepare($sql);
        $stmt->bind_param($type . 'i', $newValue, $id);
    }

    $stmt->execute();

    echo json_encode([
        "status"         => "success",
        "message"        => "Record updated",
        "affected_rows"  => $stmt->affected_rows,
        "id"             => $id,
        "field"          => $field,
        "column"         => $column,
        "new_value"      => $newValue,
    ], JSON_UNESCAPED_UNICODE);

    $stmt->close();

} catch (Throwable $e) {
    http_response_code(400);
    echo json_encode([
        "status"  => "error",
        "message" => $e->getMessage(),
        "input"   => $json ?? null
    ]);
} finally {
    $conn->close();
}
