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
            background-color: #f8f9fa;
            color: #333;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 2px 15px rgba(0, 0, 0, 0.1);
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
        }
        
        .logout-btn {
            background-color: rgba(255, 255, 255, 0.15);
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.3);
            padding: 8px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            transition: all 0.3s;
            font-size: 14px;
        }
        
        .logout-btn:hover {
            background-color: rgba(255, 255, 255, 0.25);
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
            background-color: white;
            border-radius: 10px;
            padding: 25px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
            text-align: center;
            min-width: 220px;
            border-top: 4px solid #667eea;
        }
        
        .stats-card h3 {
            color: #666;
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
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 15px;
            background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="%23666" viewBox="0 0 16 16"><path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/></svg>');
            background-repeat: no-repeat;
            background-position: 15px center;
            background-size: 16px;
        }
        
        .category-filter {
            min-width: 200px;
        }
        
        .category-filter select {
            width: 100%;
            padding: 14px 15px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 15px;
            background-color: white;
            cursor: pointer;
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
            background-color: #f8f9fa;
            color: #667eea;
            border: 1px solid #667eea;
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
            background-color: #667eea;
            color: white;
        }
        
        .data-table-container {
            background-color: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
            margin-bottom: 40px;
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            min-width: 1000px;
        }
        
        thead {
            background-color: #f8f9ff;
        }
        
        th {
            padding: 18px 15px;
            text-align: left;
            font-weight: 600;
            color: #555;
            border-bottom: 2px solid #eee;
            white-space: nowrap;
        }
        
        td {
            padding: 16px 15px;
            border-bottom: 1px solid #eee;
        }
        
        tbody tr:hover {
            background-color: #f8f9ff;
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
            background-color: #e8f5e9;
            color: #2e7d32;
        }
        
        .category-pimped {
            background-color: #fff3e0;
            color: #ef6c00;
        }
        
        .no-data {
            text-align: center;
            padding: 60px 20px;
            color: #777;
        }
        
        .no-data h3 {
            margin-bottom: 10px;
            color: #555;
        }
        
        .action-buttons {
            display: flex;
            gap: 10px;
            margin-top: 20px;
            justify-content: center;
        }
        
        .export-btn {
            background-color: #10b981;
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
            background-color: #0da271;
            transform: translateY(-1px);
        }
        
        .footer {
            text-align: center;
            margin-top: 40px;
            padding: 20px;
            color: #666;
            font-size: 14px;
            border-top: 1px solid #eee;
        }
        
        @media (max-width: 768px) {
            .dashboard-header {
                flex-direction: column;
                align-items: stretch;
            }
            
            .stats-card {
                width: 100%;
            }
            
            .filters {
                flex-direction: column;
            }
            
            .search-box, .category-filter {
                min-width: 100%;
            }
            
            .header {
                flex-direction: column;
                gap: 15px;
                text-align: center;
                padding: 20px;
            }
            
            .user-info {
                flex-direction: column;
                gap: 10px;
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
                    <p style="font-size: 12px; color: #888; margin-top: 5px;">
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
            <a href="#" class="export-btn" onclick="exportToCSV()">
                📊 CSV Export
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