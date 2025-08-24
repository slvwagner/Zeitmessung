<?php
// Database connection settings
$servername = "localhost";
$username   = "root";
$password   = "";
$dbname     = "zeitmessung_V2";

// Create connection
$conn = new mysqli($servername, $username, $password, $dbname);

// Check connection
if ($conn->connect_error) {
    die("Connection failed: " . $conn->connect_error);
}

// Set charset to utf8mb4 for proper Unicode support
$conn->set_charset("utf8mb4");

// Check if form was submitted
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    // Get and sanitize form data
    $name = $conn->real_escape_string(trim($_POST['name'] ?? ''));
    $vorname = $conn->real_escape_string(trim($_POST['vorname'] ?? ''));
    $nickname = $conn->real_escape_string(trim($_POST['nickname'] ?? ''));
    $phone = $conn->real_escape_string(trim($_POST['phone'] ?? ''));
    $email = $conn->real_escape_string(trim($_POST['email'] ?? ''));
    $kategorie = $conn->real_escape_string(trim($_POST['kategorie'] ?? ''));
    $gewicht = !empty($_POST['gewicht']) ? (float)$_POST['gewicht'] : null;

    // Prepare SQL statement
    $sql = "INSERT INTO participant (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Gewicht) 
            VALUES (?, ?, ?, ?, ?, ?, ?)";

    $stmt = $conn->prepare($sql);
    $stmt->bind_param("ssssssd", $name, $vorname, $nickname, $phone, $email, $kategorie, $gewicht);

    // Execute the statement
    if ($stmt->execute()) {
        $startnummer = $stmt->insert_id;
        $success_message = "Registration successful!<br><br>";
        $success_message .= "<strong>Startnummer:</strong> " . $startnummer . "<br>";
        $success_message .= "<strong>Name:</strong> " . htmlspecialchars($name) . "<br>";
        $success_message .= "<strong>Vorname:</strong> " . htmlspecialchars($vorname) . "<br>";
        $success_message .= "<strong>Nickname:</strong> " . htmlspecialchars($nickname) . "<br>";
        $success_message .= "<strong>Phone:</strong> " . htmlspecialchars($phone) . "<br>";
        $success_message .= "<strong>E-mail:</strong> " . htmlspecialchars($email) . "<br>";
        $success_message .= "<strong>Kategorie:</strong> " . htmlspecialchars($kategorie) . "<br>";
        if ($gewicht !== null) {
            $success_message .= "<strong>Gewicht:</strong> " . htmlspecialchars($gewicht) . " kg<br>";
        }
        $success_message .= "<strong>Registration Date:</strong> " . date('Y-m-d H:i:s');
    } else {
        $error_message = "Error: " . $stmt->error;
    }

    $stmt->close();
}

$conn->close();
?>

<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Participant Registration</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f4; }
        .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        h1 { color: #333; text-align: center; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="text"], input[type="email"], input[type="number"] { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        button { background-color: #4CAF50; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background-color: #45a049; }
        .success { background-color: #dff0d8; color: #3c763d; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .error { background-color: #f2dede; color: #a94442; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .back-link { display: block; margin-top: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Participant Registration</h1>

        <?php if (isset($success_message)): ?>
            <div class="success">
                <?php echo $success_message; ?>
            </div>
            <a href="<?php echo $_SERVER['PHP_SELF']; ?>" class="back-link">Register another participant</a>
        <?php elseif (isset($error_message)): ?>
            <div class="error">
                <?php echo $error_message; ?>
            </div>
        <?php endif; ?>

        <?php if (!isset($success_message)): ?>
        <form method="POST" action="<?php echo $_SERVER['PHP_SELF']; ?>">
            <div class="form-group">
                <label for="name">Name:</label>
                <input type="text" id="name" name="name" required>
            </div>

            <div class="form-group">
                <label for="vorname">Vorname:</label>
                <input type="text" id="vorname" name="vorname" required>
            </div>

            <div class="form-group">
                <label for="nickname">Nickname:</label>
                <input type="text" id="nickname" name="nickname">
            </div>

            <div class="form-group">
                <label for="phone">Phone:</label>
                <input type="text" id="phone" name="phone">
            </div>

            <div class="form-group">
                <label for="email">E-mail:</label>
                <input type="email" id="email" name="email">
            </div>

            <div class="form-group">
                <label for="kategorie">Kategorie:</label>
                <input type="text" id="kategorie" name="kategorie">
            </div>

            <div class="form-group">
                <label for="gewicht">Gewicht (kg):</label>
                <input type="number" id="gewicht" name="gewicht" step="0.1" min="0">
            </div>

            <button type="submit">Register Participant</button>
        </form>
        <?php endif; ?>
    </div>
</body>
</html>
