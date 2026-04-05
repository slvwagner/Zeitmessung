// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
// read.php — read rows from zeitmessung_V2.race with optional filters
header('Content-Type: application/json; charset=UTF-8');

// === DB CONFIG ===
$DB_HOST = 'localhost';
$DB_NAME = 'zeitmessung';
$DB_USER = 'root';
$DB_PASS = '';

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
    echo json_encode(["status"=>"error","message"=>"DB connection failed: ".$e->getMessage()]);
    exit;
}

// ---- Filters ----
$Startnummer  = isset($_GET['Startnummer'])  ? (int)$_GET['Startnummer']     : null;
$race_status  = isset($_GET['race_status'])  ? substr((string)$_GET['race_status'], 0, 50) : null;
$device_id    = isset($_GET['device_id'])    ? substr((string)$_GET['device_id'], 0, 32)   : null;
$min_id       = isset($_GET['since_id'])     ? (int)$_GET['since_id']       : null;
$run_min      = isset($_GET['run_min'])      ? (int)$_GET['run_min']        : null;
$run_max      = isset($_GET['run_max'])      ? (int)$_GET['run_max']        : null;
$limit        = isset($_GET['limit'])        ? max(1, min(200, (int)$_GET['limit'])) : 50;
$order        = (isset($_GET['order']) && strtolower($_GET['order']) === 'asc') ? 'ASC' : 'DESC';
$with_part    = isset($_GET['with_participant']) ? (bool)$_GET['with_participant'] : false;

$where = [];
$args  = [];

// Build WHERE
if ($Startnummer !== null && $Startnummer > 0) {
    $where[] = "r.Startnummer = :sn";
    $args[':sn'] = $Startnummer;
}
if ($race_status !== null && $race_status !== '') {
    $where[] = "r.race_status = :rs";
    $args[':rs'] = $race_status;
}
if ($device_id !== null && $device_id !== '') {
    $where[] = "r.device_id = :did";
    $args[':did'] = $device_id;
}
if ($min_id !== null && $min_id > 0) {
    $where[] = "r.id > :minid";
    $args[':minid'] = $min_id;
}
if ($run_min !== null) {
    $where[] = "r.run >= :rmin";
    $args[':rmin'] = $run_min;
}
if ($run_max !== null) {
    $where[] = "r.run <= :rmax";
    $args[':rmax'] = $run_max;
}

$select = "r.id, r.Startnummer, r.run, r.timestamp_ms, r.device_id, r.device_name, r.race_status, r.created_at, r.last_updated";
$join   = "";
if ($with_part) {
    $select .= ", p.Name, p.Vorname, p.Nickname, p.`E-mail`, p.Kategorie, p.Geburtsdatum, p.Gewicht";
    $join = "LEFT JOIN participant p ON p.Startnummer = r.Startnummer";
}

$sql = "SELECT $select FROM race r $join";
if ($where) $sql .= " WHERE " . implode(" AND ", $where);
$sql .= " ORDER BY r.id $order LIMIT :lim";

try {
    $stmt = $pdo->prepare($sql);
    foreach ($args as $k => $v) $stmt->bindValue($k, $v);
    $stmt->bindValue(':lim', $limit, PDO::PARAM_INT);
    $stmt->execute();
    $rows = $stmt->fetchAll();
    echo json_encode(["status"=>"success","data"=>$rows]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(["status"=>"error","message"=>$e->getMessage()]);
}
?>