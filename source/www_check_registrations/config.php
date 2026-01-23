<?php
// Database configuration - Update with your actual credentials
define('DB_HOST', 'localhost');
define('DB_USER', 'hostpo18_register_user');  // Your database username
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

// Check if users table exists, if not create it
function checkUsersTable() {
    $conn = getDBConnection();
    $result = $conn->query("SHOW TABLES LIKE 'users'");
    
    if ($result->num_rows == 0) {
        // Create users table
        $sql = "CREATE TABLE users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,
            email VARCHAR(100),
            role ENUM('admin', 'user') DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )";
        
        if ($conn->query($sql) === TRUE) {
            // Insert default admin user
            $password_hash = password_hash('admin123', PASSWORD_DEFAULT);
            $insert_sql = "INSERT INTO users (username, password, email, role) 
                          VALUES ('admin', '$password_hash', 'admin@example.com', 'admin')";
            $conn->query($insert_sql);
        }
    }
    
    $conn->close();
}

// Call this function to ensure users table exists
checkUsersTable();
?>
