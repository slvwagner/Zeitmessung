<?php
declare(strict_types=1);

// Database configuration
$DB_HOST = 'localhost';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'zeitmessung';

// Handle POST requests to update race status
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    // Initialize database connection
    $mysqli = new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
    if ($mysqli->connect_errno) {
        die(json_encode(['status' => 'error', 'message' => 'Database connection failed']));
    }
    $mysqli->set_charset('utf8mb4');
    
    // Get the action from POST data
    $action = $_POST['action'] ?? '';
    $new_value = '';
    
    // Determine new value based on action
    if ($action === 'start') {
        $new_value = '1';
    } elseif ($action === 'stop') {
        $new_value = '0';
    } else {
        echo json_encode(['status' => 'error', 'message' => 'Invalid action']);
        exit;
    }
    
    // Update the database
    $sql = "INSERT INTO race_management (name, value) VALUES ('Rennstatus', ?) 
            ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW()";
    
    $stmt = $mysqli->prepare($sql);
    $stmt->bind_param('s', $new_value);
    
    if ($stmt->execute()) {
        echo json_encode([
            'status' => 'success', 
            'message' => 'Race ' . ($action === 'start' ? 'started' : 'stopped'),
            'new_status' => $new_value
        ]);
    } else {
        echo json_encode(['status' => 'error', 'message' => 'Database update failed']);
    }
    
    $stmt->close();
    $mysqli->close();
    exit;
}

// Get current race status for display
$mysqli = new mysqli($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
if (!$mysqli->connect_errno) {
    $mysqli->set_charset('utf8mb4');
    $result = $mysqli->query("SELECT value FROM race_management WHERE name = 'Rennstatus'");
    if ($result && $result->num_rows > 0) {
        $row = $result->fetch_assoc();
        $current_status = $row['value'];
    } else {
        $current_status = '1'; // Default to running
    }
    $mysqli->close();
} else {
    $current_status = '1'; // Default on error
}

// Determine status text and colors
if ($current_status == '1') {
    $status_text = "Rennen läuft";
    $status_color = "#10b981"; // Green
    $bg_gradient = "linear-gradient(135deg, #1e3a8a 0%, #1e40af 100%)"; // Blue gradient
    $button_start_disabled = true;
    $button_stop_disabled = false;
} else {
    $status_text = "Rennen gestoppt";
    $status_color = "#ef4444"; // Red
    $bg_gradient = "linear-gradient(135deg, #7f1d1d 0%, #991b1b 100%)"; // Red gradient
    $button_start_disabled = false;
    $button_stop_disabled = true;
}
?>
<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Race Control - Zeitmessung</title>
    <link rel="icon" type="image/x-icon" href="favicon.ico">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
        }

        body {
            background: <?php echo $bg_gradient; ?>;
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            transition: background 0.5s ease;
            padding: 20px;
        }

        .container {
            background: rgba(30, 41, 59, 0.9);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3),
                        0 0 0 1px rgba(255, 255, 255, 0.05);
            max-width: 500px;
            width: 100%;
            text-align: center;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .header {
            margin-bottom: 30px;
        }

        h1 {
            font-size: 2.5rem;
            background: linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            margin-bottom: 10px;
            font-weight: 800;
        }

        .subtitle {
            color: #94a3b8;
            font-size: 1.1rem;
            margin-bottom: 30px;
        }

        .status-indicator {
            background: rgba(15, 23, 42, 0.7);
            border-radius: 15px;
            padding: 25px;
            margin: 30px 0;
            border: 2px solid rgba(255, 255, 255, 0.05);
            position: relative;
            overflow: hidden;
        }

        .status-indicator::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: <?php echo $status_color; ?>;
        }

        .status-label {
            color: #cbd5e1;
            font-size: 1rem;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }

        .status-text {
            font-size: 2rem;
            font-weight: 700;
            color: <?php echo $status_color; ?>;
            text-shadow: 0 0 20px <?php echo $status_color . '40'; ?>;
            margin: 10px 0;
        }

        .status-icon {
            font-size: 4rem;
            margin: 20px 0;
            filter: drop-shadow(0 0 10px <?php echo $status_color . '40'; ?>);
        }

        .buttons {
            display: flex;
            gap: 20px;
            margin-top: 30px;
        }

        .btn {
            flex: 1;
            padding: 20px;
            border: none;
            border-radius: 15px;
            font-size: 1.2rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: none !important;
        }

        .btn-start {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            color: white;
            box-shadow: 0 10px 20px rgba(16, 185, 129, 0.3);
        }

        .btn-start:hover:not(:disabled) {
            transform: translateY(-3px);
            box-shadow: 0 15px 30px rgba(16, 185, 129, 0.4);
        }

        .btn-stop {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            color: white;
            box-shadow: 0 10px 20px rgba(239, 68, 68, 0.3);
        }

        .btn-stop:hover:not(:disabled) {
            transform: translateY(-3px);
            box-shadow: 0 15px 30px rgba(239, 68, 68, 0.4);
        }

        .btn-icon {
            font-size: 1.5rem;
        }

        .last-updated {
            margin-top: 30px;
            color: #64748b;
            font-size: 0.9rem;
            padding-top: 20px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }

        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 25px;
            border-radius: 10px;
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            display: none;
            align-items: center;
            gap: 15px;
            z-index: 1000;
            animation: slideIn 0.3s ease;
        }

        .notification.show {
            display: flex;
        }

        .notification.success {
            border-left: 5px solid #10b981;
        }

        .notification.error {
            border-left: 5px solid #ef4444;
        }

        .notification-icon {
            font-size: 1.5rem;
        }

        @keyframes slideIn {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }

        .pulse {
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% {
                opacity: 1;
            }
            50% {
                opacity: 0.7;
            }
            100% {
                opacity: 1;
            }
        }

        @media (max-width: 600px) {
            .container {
                padding: 25px;
                margin: 10px;
            }
            
            .buttons {
                flex-direction: column;
            }
            
            h1 {
                font-size: 2rem;
            }
        }
    </style>
    <script>
        // Show notification
        function showNotification(message, type = 'success') {
            const notification = document.getElementById('notification');
            const notificationText = document.getElementById('notification-text');
            const notificationIcon = document.getElementById('notification-icon');
            
            notificationText.textContent = message;
            notification.className = `notification ${type}`;
            notification.classList.add('show');
            
            if (type === 'success') {
                notificationIcon.innerHTML = '✅';
            } else {
                notificationIcon.innerHTML = '❌';
            }
            
            setTimeout(() => {
                notification.classList.remove('show');
            }, 3000);
        }

        // Update race status
        async function updateRaceStatus(action) {
            try {
                const response = await fetch('race_control.php', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `action=${action}`
                });
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    showNotification(result.message, 'success');
                    // Reload page to update status display
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                } else {
                    showNotification(result.message || 'Fehler aufgetreten', 'error');
                }
            } catch (error) {
                console.error('Error:', error);
                showNotification('Netzwerkfehler', 'error');
            }
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Space bar to toggle race status
            if (e.code === 'Space') {
                e.preventDefault();
                const currentStatus = '<?php echo $current_status; ?>';
                if (currentStatus === '1') {
                    updateRaceStatus('stop');
                } else {
                    updateRaceStatus('start');
                }
            }
            
            // S key to stop race
            if (e.key === 's' || e.key === 'S') {
                e.preventDefault();
                const stopBtn = document.querySelector('.btn-stop');
                if (!stopBtn.disabled) {
                    updateRaceStatus('stop');
                }
            }
            
            // R key to start race
            if (e.key === 'r' || e.key === 'R') {
                e.preventDefault();
                const startBtn = document.querySelector('.btn-start');
                if (!startBtn.disabled) {
                    updateRaceStatus('start');
                }
            }
        });

        // Update time display
        function updateTime() {
            const now = new Date();
            const timeElement = document.getElementById('current-time');
            timeElement.textContent = now.toLocaleTimeString('de-CH', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        }

        // Update time every second
        setInterval(updateTime, 1000);
        updateTime(); // Initial call
    </script>
</head>
<body>
    <!-- Notification -->
    <div id="notification" class="notification">
        <span id="notification-icon" class="notification-icon"></span>
        <span id="notification-text"></span>
    </div>

    <div class="container">
        <div class="header">
            <h1>RACE CONTROL</h1>
            <div class="subtitle">Zeitmessung System</div>
        </div>

        <div class="status-indicator">
            <div class="status-label">Aktueller Status</div>
            <div class="status-icon">
                <?php if ($current_status == '1'): ?>
                    🏁
                <?php else: ?>
                    🚫
                <?php endif; ?>
            </div>
            <div class="status-text <?php echo $current_status == '1' ? 'pulse' : ''; ?>">
                <?php echo $status_text; ?>
            </div>
            <div style="margin-top: 15px; font-size: 0.9rem; color: #94a3b8;">
                Status-Code: <strong><?php echo $current_status; ?></strong>
            </div>
        </div>

        <div class="buttons">
            <button class="btn btn-start" 
                    onclick="updateRaceStatus('start')"
                    <?php if ($button_start_disabled) echo 'disabled'; ?>>
                <span class="btn-icon">▶️</span>
                START RENNEN
            </button>
            
            <button class="btn btn-stop" 
                    onclick="updateRaceStatus('stop')"
                    <?php if ($button_stop_disabled) echo 'disabled'; ?>>
                <span class="btn-icon">⏹️</span>
                STOPPEN
            </button>
        </div>

        <div class="last-updated">
            <div>Letzte Aktualisierung: <span id="current-time">--:--:--</span></div>
            <div style="margin-top: 5px; font-size: 0.8rem;">
                Tastenkürzel: <strong>Leertaste</strong> = Umschalten • <strong>S</strong> = Stopp • <strong>R</strong> = Start
            </div>
        </div>
    </div>
</body>
</html>
