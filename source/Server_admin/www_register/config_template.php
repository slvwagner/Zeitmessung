<?php
// Database configuration - Update with your actual credentials
define('DB_HOST', 'localhost');
define('DB_USER', 'register_user');  // Your database username
define('DB_PASSWORD', 'your_password_here');  // Your database password
define('DB_NAME', 'hostpo18_register');

// Start session if not already started
if (session_status() === PHP_SESSION_NONE) {
    session_start();
}

// Create database connection
function getDBConnection() {
    $conn = new mysqli(DB_HOST, DB_USER, DB_PASSWORD, DB_NAME);
    
    if ($conn->connect_error) {
        die("Connection failed: " . $conn->connect_error);
    }
    
    return $conn;
}
?>
