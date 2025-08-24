<?php
// Database connection settings
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

// Initialize variables
$message = '';
$participants = [];

try {
    // Connect to database using PDO
    $pdo = new PDO("mysql:host=$servername;dbname=$dbname;charset=utf8mb4", $username, $password);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Handle form submission
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
        $name = trim($_POST['name'] ?? '');
        $vorname = trim($_POST['vorname'] ?? '');
        $nickname = trim($_POST['nickname'] ?? '');
        $phone = trim($_POST['phone'] ?? '');
        $email = trim($_POST['email'] ?? '');
        $kategorie = trim($_POST['kategorie'] ?? '');
        $gewicht = trim($_POST['gewicht'] ?? '');
        $race_order = trim($_POST['race_order'] ?? '');
        
        if (!empty($name) || !empty($vorname) || !empty($nickname)) {
            // Prepare and execute SQL statement
            $stmt = $pdo->prepare("INSERT INTO participant (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Gewicht, race_order) 
                                   VALUES (:name, :vorname, :nickname, :phone, :email, :kategorie, :gewicht, :race_order)");
            $stmt->bindParam(':name', $name);
            $stmt->bindParam(':vorname', $vorname);
            $stmt->bindParam(':nickname', $nickname);
            $stmt->bindParam(':phone', $phone);
            $stmt->bindParam(':email', $email);
            $stmt->bindParam(':kategorie', $kategorie);
            $stmt->bindParam(':gewicht', $gewicht);
            $stmt->bindParam(':race_order', $race_order);
            $stmt->execute();
            
            $message = "Participant '$name $vorname' saved successfully!";
        } else {
            $message = "Please enter at least a name, first name, or nickname!";
        }
    }

    // Query participant table ordered by race_order
    $stmt = $pdo->query("SELECT * FROM participant ORDER BY race_order ASC, Startnummer ASC");
    $participants = $stmt->fetchAll(PDO::FETCH_ASSOC);

} catch (PDOException $e) {
    $message = "❌ Database error: " . $e->getMessage();
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Participant Management System</title>
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #1a2a6c 0%, #2a5298 100%);
            color: #333;
            min-height: 100vh;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .container {
            width: 95%;
            max-width: 1400px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            overflow: hidden;
            margin: 20px 0;
        }
        
        header {
            background: #2c3e50;
            color: white;
            padding: 20px;
            text-align: center;
        }
        
        h1 {
            font-size: 2.2rem;
            margin-bottom: 10px;
        }
        
        .subtitle {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        
        .content {
            display: flex;
            flex-wrap: wrap;
        }
        
        .form-section {
            flex: 1;
            min-width: 350px;
            padding: 25px;
            background: #f9f9f9;
            border-right: 1px solid #eee;
        }
        
        .data-section {
            flex: 2;
            min-width: 500px;
            padding: 25px;
            overflow-x: auto;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #444;
        }
        
        input, select {
            width: 100%;
            padding: 12px 15px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        
        input:focus, select:focus {
            border-color: #3498db;
            outline: none;
            box-shadow: 0 0 0 2px rgba(52, 152, 219, 0.1);
        }
        
        button {
            background: #3498db;
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
            width: 100%;
            transition: background 0.3s;
        }
        
        button:hover {
            background: #2980b9;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 14px;
        }
        
        th, td {
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        
        th {
            background-color: #2c3e50;
            color: white;
            font-weight: 600;
            position: sticky;
            top: 0;
        }
        
        tr:nth-child(even) {
            background-color: #f9f9f9;
        }
        
        tr:hover {
            background-color: #f0f7ff;
        }
        
        .message {
            padding: 15px;
            margin: 20px 0;
            border-radius: 6px;
            text-align: center;
            font-weight: 500;
        }
        
        .success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .actions {
            display: flex;
            gap: 5px;
        }
        
        .btn-edit, .btn-delete {
            padding: 5px 10px;
            font-size: 12px;
            width: auto;
        }
        
        .btn-edit {
            background: #f39c12;
        }
        
        .btn-edit:hover {
            background: #e67e22;
        }
        
        .btn-delete {
            background: #e74c3c;
        }
        
        .btn-delete:hover {
            background: #c0392b;
        }
        
        footer {
            text-align: center;
            margin-top: 20px;
            color: white;
            opacity: 0.8;
        }
        
        @media (max-width: 768px) {
            .content {
                flex-direction: column;
            }
            
            .form-section {
                border-right: none;
                border-bottom: 1px solid #eee;
            }
            
            table {
                font-size: 12px;
            }
            
            th, td {
                padding: 8px 10px;
            }
        }
        
        .stats {
            display: flex;
            justify-content: space-around;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        
        .stat-card {
            background: white;
            border-radius: 8px;
            padding: 15px;
            margin: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            text-align: center;
            min-width: 150px;
        }
        
        .stat-value {
            font-size: 24px;
            font-weight: bold;
            color: #2c3e50;
        }
        
        .stat-label {
            font-size: 14px;
            color: #7f8c8d;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Participant Management System</h1>
            <p class="subtitle">Manage race participants and their information</p>
        </header>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value"><?php echo count($participants); ?></div>
                <div class="stat-label">Total Participants</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">
                    <?php 
                    $withRaceOrder = array_filter($participants, function($p) { 
                        return !empty($p['race_order']); 
                    });
                    echo count($withRaceOrder);
                    ?>
                </div>
                <div class="stat-label">With Race Order</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">
                    <?php 
                    $categories = array_unique(array_column($participants, 'Kategorie'));
                    echo count($categories);
                    ?>
                </div>
                <div class="stat-label">Categories</div>
            </div>
        </div>
        
        <div class="content">
            <div class="form-section">
                <h2>Add New Participant</h2>
                <form method="POST" action="">
                    <div class="form-group">
                        <label for="name">Name</label>
                        <input type="text" id="name" name="name" placeholder="Enter last name">
                    </div>
                    
                    <div class="form-group">
                        <label for="vorname">Vorname</label>
                        <input type="text" id="vorname" name="vorname" placeholder="Enter first name">
                    </div>
                    
                    <div class="form-group">
                        <label for="nickname">Nickname</label>
                        <input type="text" id="nickname" name="nickname" placeholder="Enter nickname">
                    </div>
                    
                    <div class="form-group">
                        <label for="phone">Phone</label>
                        <input type="text" id="phone" name="phone" placeholder="Enter phone number">
                    </div>
                    
                    <div class="form-group">
                        <label for="email">E-mail</label>
                        <input type="email" id="email" name="email" placeholder="Enter email address">
                    </div>
                    
                    <div class="form-group">
                        <label for="kategorie">Kategorie</label>
                        <input type="text" id="kategorie" name="kategorie" placeholder="Enter category">
                    </div>
                    
                    <div class="form-group">
                        <label for="gewicht">Gewicht</label>
                        <input type="number" id="gewicht" name="gewicht" step="0.1" placeholder="Enter weight">
                    </div>
                    
                    <div class="form-group">
                        <label for="race_order">Race Order</label>
                        <input type="number" id="race_order" name="race_order" placeholder="Enter race order">
                    </div>
                    
                    <button type="submit" name="submit">Save Participant</button>
                </form>
                
                <?php if (!empty($message)): ?>
                    <div class="message <?php echo strpos($message, '❌') === false ? 'success' : 'error'; ?>">
                        <?php echo $message; ?>
                    </div>
                <?php endif; ?>
            </div>
            
            <div class="data-section">
                <h2>Participant List</h2>
                <p>All participants ordered by race order and start number:</p>
                
                <?php if (!empty($participants)): ?>
                    <table>
                        <thead>
                            <tr>
                                <th>Startnr</th>
                                <th>Race Order</th>
                                <th>Name</th>
                                <th>Vorname</th>
                                <th>Nickname</th>
                                <th>Phone</th>
                                <th>E-mail</th>
                                <th>Kategorie</th>
                                <th>Gewicht</th>
                                <th>Created</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php foreach ($participants as $row): ?>
                                <tr>
                                    <td><?php echo htmlspecialchars($row['Startnummer']); ?></td>
                                    <td><?php echo htmlspecialchars($row['race_order']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Name']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Vorname']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Nickname']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Phone']); ?></td>
                                    <td><?php echo htmlspecialchars($row['E-mail']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Kategorie']); ?></td>
                                    <td><?php echo htmlspecialchars($row['Gewicht']); ?></td>
                                    <td><?php echo htmlspecialchars($row['created_at']); ?></td>
                                    <td class="actions">
                                        <button class="btn-edit">Edit</button>
                                        <button class="btn-delete">Delete</button>
                                    </td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php else: ?>
                    <p>No participants found in the database.</p>
                <?php endif; ?>
            </div>
        </div>
    </div>
    
    <footer>
        <p>Participant Management System | Connected to: <?php echo $dbname; ?></p>
    </footer>

    <script>
        // Simple form validation
        document.querySelector('form').addEventListener('submit', function(e) {
            const name = document.getElementById('name').value;
            const vorname = document.getElementById('vorname').value;
            const nickname = document.getElementById('nickname').value;
            
            if (!name && !vorname && !nickname) {
                e.preventDefault();
                alert('Please enter at least a name, first name, or nickname');
                return false;
            }
        });
        
        // Add some interactivity to the table
        document.querySelectorAll('tr').forEach(row => {
            row.addEventListener('click', function() {
                this.style.backgroundColor = this.style.backgroundColor === 'rgb(240, 247, 255)' ? '' : '#f0f7ff';
            });
        });
    </script>
</body>
</html>
