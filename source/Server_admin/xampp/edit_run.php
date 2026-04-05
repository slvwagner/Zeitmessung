// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.2';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
// edit_run.php — update participant's next_run and last_run columns
header('Content-Type: application/json; charset=UTF-8');

// === DB CONFIG ===
$DB_HOST = '127.0.0.1';
$DB_NAME = 'zeitmessung';
$DB_USER = 'root';
$DB_PASS = '';

// API Key validation (optional but recommended)
$API_KEY = 'YOUR_API_KEY_HERE'; // Set this in your credentials
$headers = getallheaders();
if (isset($headers['X-API-Key'])) {
    if ($headers['X-API-Key'] !== $API_KEY) {
        http_response_code(403);
        echo json_encode(["status" => "error", "message" => "Invalid API key"]);
        exit;
    }
} else {
    // For testing, you might want to make this optional
    // http_response_code(401);
    // echo json_encode(["status" => "error", "message" => "API key required"]);
    // exit;
}

try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER,
        $DB_PASS,
        [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
        ]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(["status" => "error", "message" => "DB connection failed: " . $e->getMessage()]);
    exit;
}

// Get JSON input
$raw = file_get_contents('php://input');
$data = json_decode($raw, true);
if (!is_array($data)) {
    http_response_code(400);
    echo json_encode(["status" => "error", "message" => "Invalid JSON"]);
    exit;
}

// Validate required fields
$startnummer = isset($data['Startnummer']) ? (int)$data['Startnummer'] : 0;
$run = isset($data['run']) ? (int)$data['run'] : 0;
$action = isset($data['action']) ? (string)$data['action'] : '';

if ($startnummer <= 0) {
    http_response_code(400);
    echo json_encode(["status" => "error", "message" => "Invalid Startnummer"]);
    exit;
}

if ($action === '') {
    http_response_code(400);
    echo json_encode(["status" => "error", "message" => "Action required (increment_next or set_last)"]);
    exit;
}

try {
    $pdo->beginTransaction();
    
    if ($action === 'increment_next') {
        // Increment next_run for this participant
        $stmt = $pdo->prepare("
            UPDATE participant 
            SET next_run = next_run + 1,
                last_updated = NOW(3)
            WHERE Startnummer = :snr
        ");
        $stmt->execute([':snr' => $startnummer]);
        
        $affected = $stmt->rowCount();
        if ($affected === 0) {
            // Participant not found - create with default values
            $stmt = $pdo->prepare("
                INSERT INTO participant (Startnummer, next_run, created_at, last_updated)
                VALUES (:snr, 2, NOW(), NOW(3))
                ON DUPLICATE KEY UPDATE 
                    next_run = next_run + 1,
                    last_updated = NOW(3)
            ");
            $stmt->execute([':snr' => $startnummer]);
            $affected = $stmt->rowCount();
        }
        
        // Get the updated values
        $stmt = $pdo->prepare("
            SELECT next_run, last_run 
            FROM participant 
            WHERE Startnummer = :snr
        ");
        $stmt->execute([':snr' => $startnummer]);
        $participant = $stmt->fetch();
        
        echo json_encode([
            "status" => "success",
            "message" => "next_run incremented successfully",
            "data" => [
                "Startnummer" => $startnummer,
                "next_run" => $participant['next_run'] ?? 2,
                "last_run" => $participant['last_run'] ?? null
            ]
        ]);
        
    } elseif ($action === 'set_last') {
        // Update last_run to the specified run number
        if ($run <= 0) {
            http_response_code(400);
            echo json_encode(["status" => "error", "message" => "Invalid run number for set_last action"]);
            $pdo->rollBack();
            exit;
        }
        
        $stmt = $pdo->prepare("
            UPDATE participant 
            SET last_run = :run,
                last_updated = NOW(3)
            WHERE Startnummer = :snr
        ");
        $stmt->execute([
            ':snr' => $startnummer,
            ':run' => $run
        ]);
        
        $affected = $stmt->rowCount();
        if ($affected === 0) {
            // Participant not found - create with default values
            $stmt = $pdo->prepare("
                INSERT INTO participant (Startnummer, last_run, next_run, created_at, last_updated)
                VALUES (:snr, :run, GREATEST(:run + 1, 1), NOW(), NOW(3))
                ON DUPLICATE KEY UPDATE 
                    last_run = :run,
                    next_run = GREATEST(:run + 1, COALESCE(next_run, 1)),
                    last_updated = NOW(3)
            ");
            $stmt->execute([
                ':snr' => $startnummer,
                ':run' => $run
            ]);
            $affected = $stmt->rowCount();
        }
        
        // Get the updated values
        $stmt = $pdo->prepare("
            SELECT next_run, last_run 
            FROM participant 
            WHERE Startnummer = :snr
        ");
        $stmt->execute([':snr' => $startnummer]);
        $participant = $stmt->fetch();
        
        echo json_encode([
            "status" => "success",
            "message" => "last_run updated successfully",
            "data" => [
                "Startnummer" => $startnummer,
                "next_run" => $participant['next_run'] ?? max($run + 1, 1),
                "last_run" => $participant['last_run'] ?? $run
            ]
        ]);
        
    } else {
        http_response_code(400);
        echo json_encode(["status" => "error", "message" => "Invalid action. Use 'increment_next' or 'set_last'"]);
        $pdo->rollBack();
        exit;
    }
    
    $pdo->commit();
    
} catch (PDOException $e) {
    $pdo->rollBack();
    http_response_code(500);
    echo json_encode(["status" => "error", "message" => "Database error: " . $e->getMessage()]);
    exit;
} catch (Exception $e) {
    $pdo->rollBack();
    http_response_code(500);
    echo json_encode(["status" => "error", "message" => "Error: " . $e->getMessage()]);
    exit;
}
?>



