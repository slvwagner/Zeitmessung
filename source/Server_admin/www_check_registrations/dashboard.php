// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "<div style='color: #888; font-size: small;'>[Zeitmessung] Project Version: $PROJECT_VERSION</div>\n";
<?php
require_once 'config.php';

// Check if user is logged in
if (!isset($_SESSION['user_id'])) {
    header('Location: index.php');
    exit();
}

// Get database connection
$conn = getDBConnection();

// Initialize variables
$search = isset($_GET['search']) ? trim($_GET['search']) : '';
$category = isset($_GET['category']) ? $_GET['category'] : '';

// Build the WHERE clause
$whereConditions = [];
$params = [];
$types = '';

if (!empty($search)) {
    // Use 'E-mail' with quotes because it contains a hyphen
    $whereConditions[] = "(Name LIKE ? OR Vorname LIKE ? OR `E-mail` LIKE ? OR Kategorie LIKE ?)";
    $searchTerm = "%$search%";
    $params = [$searchTerm, $searchTerm, $searchTerm, $searchTerm];
    $types = 'ssss';
}

if (!empty($category) && $category !== '') {
    $whereConditions[] = "Kategorie = ?";
    $params[] = $category;
    $types .= 's';
}

$whereClause = '';
if (!empty($whereConditions)) {
    $whereClause = 'WHERE ' . implode(' AND ', $whereConditions);
}

// Fetch data from participants table
$sql = "SELECT * FROM participants $whereClause ORDER BY Registrierungsnummer DESC";
$stmt = $conn->prepare($sql);

if (!empty($params)) {
    $stmt->bind_param($types, ...$params);
}

$stmt->execute();
$result = $stmt->get_result();

// Get total participants count
$countSql = "SELECT COUNT(*) as total FROM participants $whereClause";
$countStmt = $conn->prepare($countSql);

if (!empty($params)) {
    $countStmt->bind_param($types, ...$params);
}

$countStmt->execute();
$countResult = $countStmt->get_result();
$rowCount = $countResult->fetch_assoc();
$totalParticipants = $rowCount['total'];

// Get latest registration date (unfiltered)
$latestSql = "SELECT created_at FROM participants ORDER BY created_at DESC LIMIT 1";
$latestResult = $conn->query($latestSql);
$latestRow = $latestResult->fetch_assoc();
$latestDate = $latestRow ? $latestRow['created_at'] : null;

// Get unique categories for filter
$categoriesResult = $conn->query("SELECT DISTINCT Kategorie FROM participants WHERE Kategorie IS NOT NULL ORDER BY Kategorie");
$categories = [];
while ($row = $categoriesResult->fetch_assoc()) {
    $categories[] = $row['Kategorie'];
}
?>

<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - Teilnehmer Registrierung</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #121212;
            color: #e0e0e0;
            min-height: 100vh;
        }
        
        .header {
            background: linear-gradient(135deg, #2d3748 0%, #4a5568 100%);
            color: #fff;
            padding: 20px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            border-bottom: 1px solid #2d3748;
        }
        
        .header h1 {
            font-size: 22px;
            font-weight: 600;
        }
        
        .user-info {
            display: flex;
            align-items: center;
            gap: 20px;
        }
        
        .user-info span {
            font-size: 14px;
            color: #cbd5e0;
        }
        
        .logout-btn {
            background-color: rgba(255, 255, 255, 0.1);
            color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.2);
            padding: 8px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            transition: all 0.3s;
            font-size: 14px;
        }
        
        .logout-btn:hover {
            background-color: rgba(255, 255, 255, 0.2);
            transform: translateY(-1px);
        }
        
        .container {
            max-width: 1400px;
            margin: 30px auto;
            padding: 0 20px;
        }
        
        .dashboard-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            flex-wrap: wrap;
            gap: 20px;
        }
        
        .stats-card {
            background-color: #1e1e1e;
            border-radius: 10px;
            padding: 25px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
            text-align: center;
            min-width: 220px;
            border-top: 4px solid #667eea;
            transition: transform 0.3s;
        }
        
        .stats-card:hover {
            transform: translateY(-5px);
        }
        
        .stats-card h3 {
            color: #a0aec0;
            font-size: 14px;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .stats-card .count {
            font-size: 36px;
            font-weight: 700;
            color: #667eea;
        }
        
        .filters {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .search-box {
            flex: 1;
            min-width: 300px;
            position: relative;
        }
        
        .search-box input {
            width: 100%;
            padding: 14px 15px 14px 45px;
            border: 1px solid #2d3748;
            border-radius: 8px;
            font-size: 15px;
            background-color: #1e1e1e;
            color: #e0e0e0;
            background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="%23a0aec0" viewBox="0 0 16 16"><path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/></svg>');
            background-repeat: no-repeat;
            background-position: 15px center;
            background-size: 16px;
        }
        
        .search-box input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2);
        }
        
        .search-box input::placeholder {
            color: #718096;
        }
        
        .category-filter {
            min-width: 200px;
        }
        
        .category-filter select {
            width: 100%;
            padding: 14px 15px;
            border: 1px solid #2d3748;
            border-radius: 8px;
            font-size: 15px;
            background-color: #1e1e1e;
            color: #e0e0e0;
            cursor: pointer;
        }
        
        .category-filter select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .filter-btn {
            background-color: #667eea;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0 30px;
            cursor: pointer;
            font-weight: 600;
            font-size: 15px;
            transition: all 0.3s;
        }
        
        .filter-btn:hover {
            background-color: #5a6fd8;
            transform: translateY(-1px);
        }
        
        .clear-btn {
            background-color: #2d3748;
            color: #cbd5e0;
            border: 1px solid #4a5568;
            border-radius: 8px;
            padding: 14px 20px;
            cursor: pointer;
            font-weight: 600;
            font-size: 15px;
            transition: all 0.3s;
            text-decoration: none;
            display: inline-block;
        }
        
        .clear-btn:hover {
            background-color: #4a5568;
            color: white;
        }
        
        .data-table-container {
            background-color: #1e1e1e;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
            margin-bottom: 40px;
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            min-width: 1000px;
        }
        
        thead {
            background-color: #2d3748;
        }
        
        th {
            padding: 18px 15px;
            text-align: left;
            font-weight: 600;
            color: #cbd5e0;
            border-bottom: 2px solid #4a5568;
            white-space: nowrap;
        }
        
        td {
            padding: 16px 15px;
            border-bottom: 1px solid #2d3748;
            color: #e0e0e0;
        }
        
        tbody tr {
            transition: background-color 0.2s;
        }
        
        tbody tr:hover {
            background-color: #2d3748;
        }
        
        .registrations-nr {
            font-weight: 600;
            color: #667eea;
        }
        
        .category-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .category-standard {
            background-color: rgba(72, 187, 120, 0.2);
            color: #48bb78;
        }
        
        .category-pimped {
            background-color: rgba(237, 137, 54, 0.2);
            color: #ed8936;
        }
        
        .no-data {
            text-align: center;
            padding: 60px 20px;
            color: #a0aec0;
        }
        
        .no-data h3 {
            margin-bottom: 10px;
            color: #cbd5e0;
        }
        
        .action-buttons {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            justify-content: center;
        }
        
        .export-btn {
            background-color: #38a169;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px 20px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.3s;
        }
        
        .export-btn:hover {
            background-color: #2f855a;
            transform: translateY(-1px);
        }
        
        .footer {
            text-align: center;
            margin-top: 40px;
            padding: 20px;
            color: #a0aec0;
            font-size: 14px;
            border-top: 1px solid #2d3748;
        }
        
        /* Scrollbar Styling */
        ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
        }
        
        ::-webkit-scrollbar-track {
            background: #2d3748;
            border-radius: 5px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: #4a5568;
            border-radius: 5px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: #667eea;
        }
        
        @media (max-width: 480px) {
            .container {
                padding: 0 10px;
                margin: 15px auto;
            }
            
            .stats-card {
                padding: 15px;
                min-width: auto;
            }
            
            .stats-card .count {
                font-size: 28px;
            }
            
            table {
                font-size: 14px;
            }
            
            td, th {
                padding: 12px 8px;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Teilnehmer Registrierung - Dashboard</h1>
        <div class="user-info">
            <span>Angemeldet als: <strong><?php echo htmlspecialchars($_SESSION['username']); ?></strong></span>
            <a href="logout.php" class="logout-btn">Abmelden</a>
        </div>
    </div>
    
    <div class="container">
        <div class="dashboard-header">
            <div class="stats-card">
                <h3>Gesamte Teilnehmer</h3>
                <div class="count"><?php echo $totalParticipants; ?></div>
                <?php if ($search || $category): ?>
                    <p style="font-size: 12px; color: #a0aec0; margin-top: 5px;">
                        (Gefiltert: <?php echo $result->num_rows; ?>)
                    </p>
                <?php endif; ?>
            </div>
            
            <div class="stats-card">
                <h3>Letzte Registrierung</h3>
                <div class="count" style="font-size: 18px; margin-top: 8px;">
                    <?php 
                    if ($latestDate) {
                        echo date('d.m.Y H:i', strtotime($latestDate));
                    } else {
                        echo 'Keine';
                    }
                    ?>
                </div>
            </div>
            
            <div class="stats-card">
                <h3>Benutzerrolle</h3>
                <div class="count" style="font-size: 18px; margin-top: 8px;">
                    <?php echo htmlspecialchars(ucfirst($_SESSION['role'])); ?>
                </div>
            </div>
        </div>
        
        <form method="GET" action="" class="filters">
            <div class="search-box">
                <input type="text" name="search" placeholder="Suche nach Name, Vorname, E-mail oder Kategorie..." 
                       value="<?php echo htmlspecialchars($search); ?>">
            </div>
            
            <div class="category-filter">
                <select name="category">
                    <option value="">Alle Kategorien</option>
                    <?php foreach ($categories as $cat): ?>
                        <option value="<?php echo htmlspecialchars($cat); ?>" 
                            <?php echo ($category == $cat) ? 'selected' : ''; ?>>
                            <?php echo htmlspecialchars($cat); ?>
                        </option>
                    <?php endforeach; ?>
                </select>
            </div>
            
            <button type="submit" class="filter-btn">Filter anwenden</button>
            
            <?php if ($search || $category): ?>
                <a href="dashboard.php" class="clear-btn">Filter zurücksetzen</a>
            <?php endif; ?>
        </form>
        
        <div class="action-buttons">
            <a href="export_csv.php" class="export-btn" onclick="return confirm('Möchten Sie wirklich ALLE Teilnehmerdaten exportieren?\\n\\nAktive Filter werden ignoriert.\\nDie Exportdatei enthält alle Datensätze.')">
                📊 CSV Export (Alle Daten)
            </a>
        </div>
        
        <div class="data-table-container">
            <?php if ($result->num_rows > 0): ?>
                <table>
                    <thead>
                        <tr>
                            <th>Reg.Nr.</th>
                            <th>Registriert am</th>
                            <th>Name</th>
                            <th>Vorname</th>
                            <th>Nickname</th>
                            <th>Telefon</th>
                            <th>E-mail</th>
                            <th>Kategorie</th>
                            <th>Geburtsdatum</th>
                            <th>Gewicht (kg)</th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php while ($row = $result->fetch_assoc()): ?>
                            <tr>
                                <td class="registrations-nr"><?php echo htmlspecialchars($row['Registrierungsnummer']); ?></td>
                                <td><?php echo date('d.m.Y H:i', strtotime($row['created_at'])); ?></td>
                                <td><?php echo htmlspecialchars($row['Name']); ?></td>
                                <td><?php echo htmlspecialchars($row['Vorname']); ?></td>
                                <td><?php echo htmlspecialchars($row['Nickname'] ?: '-'); ?></td>
                                <td><?php echo htmlspecialchars($row['Phone'] ?: '-'); ?></td>
                                <td><?php echo htmlspecialchars($row['E-mail'] ?: '-'); ?></td>
                                <td>
                                    <span class="category-badge category-<?php echo strtolower($row['Kategorie']); ?>">
                                        <?php echo htmlspecialchars($row['Kategorie']); ?>
                                    </span>
                                </td>
                                <td>
                                    <?php 
                                    if ($row['Geburtsdatum'] && $row['Geburtsdatum'] != '2015-01-01') {
                                        echo date('d.m.Y', strtotime($row['Geburtsdatum']));
                                    } else {
                                        echo '-';
                                    }
                                    ?>
                                </td>
                                <td>
                                    <?php 
                                    if ($row['Gewicht']) {
                                        echo htmlspecialchars($row['Gewicht']) . ' kg';
                                    } else {
                                        echo '-';
                                    }
                                    ?>
                                </td>
                            </tr>
                        <?php endwhile; ?>
                    </tbody>
                </table>
            <?php else: ?>
                <div class="no-data">
                    <h3>Keine Teilnehmer gefunden</h3>
                    <p><?php echo ($search || $category) ? 'Versuchen Sie es mit einem anderen Suchbegriff oder Filter.' : 'Es wurden noch keine Teilnehmer registriert.'; ?></p>
                </div>
            <?php endif; ?>
        </div>
        
        <div class="footer">
            <p>Teilnehmer Registrierung System • © <?php echo date('Y'); ?> • Angemeldet als: <?php echo htmlspecialchars($_SESSION['username']); ?></p>
        </div>
    </div>
    
    <script>
    function exportToCSV() {
        // Get all table data
        const table = document.querySelector('table');
        if (!table) {
            alert('Keine Daten zum Exportieren verfügbar.');
            return;
        }
        
        let csv = [];
        // Get headers
        const headers = [];
        table.querySelectorAll('thead th').forEach(th => {
            headers.push(th.textContent.trim());
        });
        csv.push(headers.join(','));
        
        // Get rows
        table.querySelectorAll('tbody tr').forEach(tr => {
            const row = [];
            tr.querySelectorAll('td').forEach(td => {
                // Remove any commas from data and trim
                let text = td.textContent.trim();
                // If text contains comma, wrap in quotes
                if (text.includes(',')) {
                    text = '"' + text + '"';
                }
                row.push(text);
            });
            csv.push(row.join(','));
        });
        
        // Create download link
        const csvContent = csv.join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', 'teilnehmer_registrierung_' + new Date().toISOString().slice(0,10) + '.csv');
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        alert('CSV-Export wurde gestartet. Die Datei wird heruntergeladen.');
    }
    </script>
    
    <?php
    // Close connections
    $stmt->close();
    $countStmt->close();
    $conn->close();
    ?>
</body>
</html>
