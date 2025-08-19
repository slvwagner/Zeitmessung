<?php
// api/race.php
header('Content-Type: application/json');

// --- DB CONFIG ---
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

// --- CONNECT ---
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
try {
    $conn = new mysqli($servername, $username, $password, $dbname);
    $conn->set_charset('utf8mb4');
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(["status" => "error", "message" => "DB connection failed", "detail" => $e->getMessage()]);
    exit;
}

// --- INPUTS ---
$race_id         = $_GET['id']              ?? null;          // race.id
$participant_id  = $_GET['participant_id']  ?? null;          // race.participant_id
$race_status     = $_GET['race_status']     ?? null;          // race.race_status
$run             = $_GET['run']             ?? null;          // race.run
$device_id       = $_GET['device_id']       ?? null;          // race.device_id
$from            = $_GET['from']            ?? null;          // ISO-like, e.g. 2025-08-19 10:00:00.123
$to              = $_GET['to']              ?? null;          // same as above
$include_part    = filter_var($_GET['include_participant'] ?? 'false', FILTER_VALIDATE_BOOLEAN);
$order           = strtolower($_GET['order'] ?? 'asc');       // asc | desc
$limit           = intval($_GET['limit'] ?? 500);
$offset          = intval($_GET['offset'] ?? 0);

if (!in_array($order, ['asc','desc'], true)) { $order = 'asc'; }
if ($limit < 1 || $limit > 5000) { $limit = 500; }
if ($offset < 0) { $offset = 0; }

// --- BASE SELECT ---
if ($include_part) {
    $select = "
        SELECT
            r.id,
            r.participant_id,
            r.run,
            r.timestamp_ms,
            r.device_id,
            r.device_name,
            r.race_status,
            r.created_at,
            r.last_updated,
            p.race_order,
            p.last_run,
            p.next_run,
            p.Name,
            p.Vorname,
            p.Nickname,
            p.Phone,
            `p`.`E-mail` AS Email,
            p.Kategorie,
            p.Gewicht
        FROM race r
        LEFT JOIN participant p ON p.id = r.participant_id
    ";
} else {
    $select = "
        SELECT
            r.id,
            r.participant_id,
            r.run,
            r.timestamp_ms,
            r.device_id,
            r.device_name,
            r.race_status,
            r.created_at,
            r.last_updated
        FROM race r
    ";
}

// --- FILTERS ---
$where  = [];
$types  = '';
$params = [];

$add = function(string $cond, string $type, $value) use (&$where, &$types, &$params) {
    $where[] = $cond;
    $types  .= $type;
    $params[] = $value;
};

if ($race_id !== null && $race_id !== '') {
    $add('r.id = ?', 'i', (int)$race_id);
}
if ($participant_id !== null && $participant_id !== '') {
    $add('r.participant_id = ?', 'i', (int)$participant_id);
}
if ($race_status !== null && $race_status !== '') {
    $add('r.race_status = ?', 's', $race_status);
}
if ($run !== null && $run !== '') {
    $add('r.run = ?', 'i', (int)$run);
}
if ($device_id !== null && $device_id !== '') {
    $add('r.device_id = ?', 's', $device_id);
}
if ($from) {
    // Accepts 'YYYY-MM-DD HH:MM:SS[.mmm]' or 'YYYY-MM-DD'
    $add('r.timestamp_ms >= ?', 's', $from);
}
if ($to) {
    $add('r.timestamp_ms <= ?', 's', $to);
}

$sql = $select;
if ($where) {
    $sql .= " WHERE " . implode(' AND ', $where);
}
$sql .= " ORDER BY r.timestamp_ms " . strtoupper($order) . " LIMIT ? OFFSET ?";

// add limit/offset bindings
$types  .= 'ii';
$params[] = $limit;
$params[] = $offset;

// --- EXECUTE ---
try {
    $stmt = $conn->prepare($sql);
    // dynamic bind_param
    $stmt->bind_param($types, ...$params);
    $stmt->execute();
    $result = $stmt->get_result();

    $data = [];
    while ($row = $result->fetch_assoc()) {
        $data[] = $row;
    }

    echo json_encode([
        "status" => "success",
        "count"  => count($data),
        "limit"  => $limit,
        "offset" => $offset,
        "data"   => $data
    ], JSON_UNESCAPED_UNICODE);

    $stmt->close();
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode([
        "status"  => "error",
        "message" => "Query failed",
        "detail"  => $e->getMessage()
    ]);
} finally {
    $conn->close();
}
