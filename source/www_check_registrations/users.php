<?php
require_once 'config.php';

$conn = getDBConnection();

// Create users table if it doesn't exist
$sql = "CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(100),
    role ENUM('admin', 'user') DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)";

if ($conn->query($sql) === TRUE) {
    echo "Users table created successfully.<br>";
    
    // Check if admin user already exists
    $check_sql = "SELECT id FROM users WHERE username = 'mutterschiff'";
    $result = $conn->query($check_sql);
    
    if ($result->num_rows == 0) {
        // Insert default admin user
        $password_hash = password_hash('admin123', PASSWORD_DEFAULT);
        $insert_sql = "INSERT INTO users (username, password, email, role) 
                      VALUES ('admin', '$password_hash', 'admin@example.com', 'admin')";
        
        if ($conn->query($insert_sql) === TRUE) {
            echo "Default admin user created successfully.<br>";
            echo "Username: mutterschiff<br>";
            echo "Password: admin123<br>";
        } else {
            echo "Error creating admin user: " . $conn->error . "<br>";
        }
    } else {
        echo "Admin user already exists.<br>";
    }
} else {
    echo "Error creating table: " . $conn->error . "<br>";
}

$conn->close();
?>