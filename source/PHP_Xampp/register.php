<?php
// Database connection settings
$servername = "lx51.hoststar.hosting";
$username   = "ch367079_flo";
$password   = "nrK4ytHA+JKNwfu";
$dbname     = "ch367079_race";

// SMTP / Mail settings
$smtp_host     = 'lx51.hoststar.hosting';
$smtp_username = 'race@kinoklub.ch';
$smtp_password = 'y6rJ@i7ryF5$XMj';
$from_email    = 'race@kinoklub.ch';
$from_name     = 'Registration System';

// Load PHPMailer
use PHPMailer\PHPMailer\PHPMailer;
use PHPMailer\PHPMailer\Exception;

require 'PHPMailer/PHPMailer.php';
require 'PHPMailer/SMTP.php';
require 'PHPMailer/Exception.php';

// Create DB connection
$conn = new mysqli($servername, $username, $password, $dbname);

// Check connection
if ($conn->connect_error) {
    die("Connection failed: " . $conn->connect_error);
}
$conn->set_charset("utf8mb4");

// Handle email verification
if (isset($_GET['token'])) {
    $token = $conn->real_escape_string($_GET['token']);
    
    $sql = "SELECT Startnummer, token_expires FROM participant WHERE verification_token = ? AND is_verified = FALSE";
    $stmt = $conn->prepare($sql);
    $stmt->bind_param("s", $token);
    $stmt->execute();
    $result = $stmt->get_result();
    
    if ($result->num_rows > 0) {
        $participant = $result->fetch_assoc();
        
        if (strtotime($participant['token_expires']) > time()) {
            $update_sql = "UPDATE participant SET is_verified = TRUE, verification_token = NULL, token_expires = NULL WHERE Startnummer = ?";
            $update_stmt = $conn->prepare($update_sql);
            $update_stmt->bind_param("i", $participant['Startnummer']);
            
            if ($update_stmt->execute()) {
                $verification_success = "Email successfully verified! Your registration is now complete.";
            } else {
                $verification_error = "Error verifying email: " . $update_stmt->error;
            }
            $update_stmt->close();
        } else {
            $verification_error = "Verification link has expired. Please register again.";
        }
    } else {
        $verification_error = "Invalid verification token or email already verified.";
    }
    $stmt->close();
}

// Handle form submission
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $name     = trim($_POST['name'] ?? '');
    $vorname  = trim($_POST['vorname'] ?? '');
    $nickname = trim($_POST['nickname'] ?? '');
    $phone    = trim($_POST['phone'] ?? '');
    $email    = filter_var(trim($_POST['email'] ?? ''), FILTER_VALIDATE_EMAIL);
    $kategorie= trim($_POST['kategorie'] ?? '');
    $gewicht  = !empty($_POST['gewicht']) ? (float)$_POST['gewicht'] : null;

    if (empty($name) || empty($vorname) || !$email) {
        $error_message = "Name, Vorname, and valid email are required.";
    } else {
        $verification_token = bin2hex(random_bytes(32));
        $token_expires = date('Y-m-d H:i:s', strtotime('+24 hours'));

        $sql = "INSERT INTO participant (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Gewicht, verification_token, token_expires) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)";
        $stmt = $conn->prepare($sql);
        $stmt->bind_param("ssssssdss", $name, $vorname, $nickname, $phone, $email, $kategorie, $gewicht, $verification_token, $token_expires);

        if ($stmt->execute()) {
            if (sendVerificationEmail($email, $name, $vorname, $verification_token, $smtp_host, $smtp_username, $smtp_password, $from_email, $from_name)) {
                $success_message = "Registration successful!<br><br>";
                $success_message .= "We've sent a verification email to <strong>" . htmlspecialchars($email) . "</strong>.<br>";
                $success_message .= "Please check your email and click the verification link to complete your registration.<br><br>";
                $success_message .= "<strong>Note:</strong> The verification link is valid for 24 hours.";
            } else {
                $error_message = "Registration successful but email sending failed. Please contact support.";
            }
        } else {
            $error_message = "Error: " . $stmt->error;
        }
        $stmt->close();
    }
}

$conn->close();

// Function to send email via PHPMailer
function sendVerificationEmail($to_email, $name, $vorname, $token, $smtp_host, $smtp_username, $smtp_password, $from_email, $from_name) {
    $verification_url = "https://" . $_SERVER['HTTP_HOST'] . $_SERVER['PHP_SELF'] . "?token=" . $token;
    $mail = new PHPMailer(true);

    try {
        $mail->isSMTP();
        $mail->Host       = $smtp_host;
        $mail->SMTPAuth   = true;
        $mail->Username   = $smtp_username;
        $mail->Password   = $smtp_password;
        $mail->SMTPSecure = PHPMailer::ENCRYPTION_STARTTLS;
        $mail->Port       = 587;

        $mail->setFrom($from_email, $from_name);
        $mail->addReplyTo($from_email, $from_name);
        $mail->addAddress($to_email, $vorname . ' ' . $name);

        $mail->isHTML(true);
        $mail->Subject = 'Verify Your Registration';
        $mail->Body    = "
            <h2>Hello $vorname $name,</h2>
            <p>Thank you for registering!</p>
            <p>Please click the link below to verify your email address:</p>
            <p><a href='$verification_url' style='background-color:#4CAF50;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;'>Verify Email</a></p>
            <p>Or copy this link to your browser:<br>$verification_url</p>
            <p><strong>This link will expire in 24 hours.</strong></p>
        ";

        return $mail->send();
    } catch (Exception $e) {
        error_log("Mailer Error: {$mail->ErrorInfo}");
        return false;
    }
}
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
        .info { background-color: #d9edf7; color: #31708f; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .back-link { display: block; margin-top: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Participant Registration</h1>

        <?php if (isset($verification_success)): ?>
            <div class="success"><?= $verification_success; ?></div>
            <a href="<?= $_SERVER['PHP_SELF']; ?>" class="back-link">Register another participant</a>
        <?php elseif (isset($verification_error)): ?>
            <div class="error"><?= $verification_error; ?></div>
            <a href="<?= $_SERVER['PHP_SELF']; ?>" class="back-link">Try again</a>
        <?php elseif (isset($success_message)): ?>
            <div class="info"><?= $success_message; ?></div>
        <?php elseif (isset($error_message)): ?>
            <div class="error"><?= $error_message; ?></div>
        <?php endif; ?>

        <?php if (!isset($success_message) && !isset($verification_success) && !isset($verification_error)): ?>
        <form method="POST" action="<?= $_SERVER['PHP_SELF']; ?>">
            <div class="form-group">
                <label for="name">Name:*</label>
                <input type="text" id="name" name="name" required>
            </div>

            <div class="form-group">
                <label for="vorname">Vorname:*</label>
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
                <label for="email">E-mail:*</label>
                <input type="email" id="email" name="email" required>
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
