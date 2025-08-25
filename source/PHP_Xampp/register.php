<?php
// Immer UTF-8-Header senden (hilft mit Umlauten usw.)
header('Content-Type: text/html; charset=UTF-8');

// === reCAPTCHA-SchlÃ¼ssel (mit echten ersetzen) ===
$RECAPTCHA_SITE_KEY   = '6Ld3uLErAAAAANkIa-qGehDMixDOGQrzDCGA0kLo';
$RECAPTCHA_SECRET_KEY = '6Ld3uLErAAAAAMdVeHUwFrXsLcn9JeiJItIiA9Qw';

// Datenbank-Verbindungsdaten
$servername = "lx51.hoststar.hosting";
$username   = "ch367079_flo";
$password   = "nrK4ytHA+JKNwfu";
$dbname     = "ch367079_race";

// Verbindung herstellen
$conn = new mysqli($servername, $username, $password, $dbname);

// Verbindung prÃ¼fen
if ($conn->connect_error) {
    die("Verbindung fehlgeschlagen: " . htmlspecialchars($conn->connect_error));
}

// Zeichensatz auf utf8mb4 setzen (fÃ¼r Umlaute etc.)
$conn->set_charset("utf8mb4");

// Helfer: reCAPTCHA serverseitig prÃ¼fen
function verify_recaptcha($secret, $response, $remoteIp = null) {
    if (empty($response)) return [false, 'Fehlende reCAPTCHA-Antwort.'];

    $url = 'https://www.google.com/recaptcha/api/siteverify';
    $postFields = http_build_query([
        'secret'   => $secret,
        'response' => $response,
        'remoteip' => $remoteIp
    ]);

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $postFields,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 10,
        ]);
        $result = curl_exec($ch);
        $err    = curl_error($ch);
        curl_close($ch);
        if ($result === false) {
            return [false, 'reCAPTCHA-ÃœberprÃ¼fung fehlgeschlagen (cURL): ' . $err];
        }
    } else {
        $context = stream_context_create([
            'http' => [
                'method'  => 'POST',
                'header'  => "Content-type: application/x-www-form-urlencoded\r\n",
                'content' => $postFields,
                'timeout' => 10
            ]
        ]);
        $result = @file_get_contents($url, false, $context);
        if ($result === false) {
            return [false, 'reCAPTCHA-ÃœberprÃ¼fung fehlgeschlagen (HTTP-Anfrage).'];
        }
    }

    $json = json_decode($result, true);
    if (!is_array($json)) {
        return [false, 'UngÃ¼ltige reCAPTCHA-Antwort von Google.'];
    }

    if (!empty($json['success'])) {
        return [true, null];
    }

    $codes = isset($json['error-codes']) ? implode(', ', (array)$json['error-codes']) : 'unbekannter_Fehler';
    return [false, 'reCAPTCHA fehlgeschlagen: ' . $codes];
}

/**
 * Normalisiert ein eingegebenes Datum in MySQL-Format (YYYY-MM-DD).
 * Akzeptierte Formate: YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY
 * Gibt [true, 'YYYY-MM-DD'] bei Erfolg, sonst [false, 'Fehlermeldung'].
 */
function normalize_birthdate($raw) {
    $raw = trim((string)$raw);
    if ($raw === '') {
        return [false, 'Bitte Geburtsdatum angeben.'];
    }

    // Versuche direktes ISO-Format
    if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $raw)) {
        $parts = explode('-', $raw);
        if (checkdate((int)$parts[1], (int)$parts[2], (int)$parts[0])) {
            $iso = $raw;
        } else {
            return [false, 'UngÃ¼ltiges Datum.'];
        }
    } else {
        // Ersetze / durch . und erwarte dann DD.MM.YYYY
        $tmp = str_replace('/', '.', $raw);
        if (preg_match('/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/', $tmp, $m)) {
            $d = (int)$m[1];
            $mth = (int)$m[2];
            $y = (int)$m[3];
            if (!checkdate($mth, $d, $y)) {
                return [false, 'UngÃ¼ltiges Datum.'];
            }
            $iso = sprintf('%04d-%02d-%02d', $y, $mth, $d);
        } else {
            return [false, 'Bitte Datum als YYYY-MM-DD oder DD.MM.YYYY eingeben.'];
        }
    }

    // Logische PrÃ¼fung: nicht in der Zukunft, realistisch alt
    $today = date('Y-m-d');
    if ($iso > $today) {
        return [false, 'Geburtsdatum darf nicht in der Zukunft liegen.'];
    }
    // Optional: Mindestjahr (z. B. 1900)
    if (substr($iso, 0, 4) < '1900') {
        return [false, 'Geburtsdatum ist unrealistisch.'];
    }

    return [true, $iso];
}

// Wenn Formular abgesendet
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    // 1) reCAPTCHA prÃ¼fen
    [$ok, $recaptcha_err] = verify_recaptcha(
        $RECAPTCHA_SECRET_KEY,
        $_POST['g-recaptcha-response'] ?? '',
        $_SERVER['REMOTE_ADDR'] ?? null
    );

    if (!$ok) {
        $error_message = $recaptcha_err ?: 'reCAPTCHA-ÃœberprÃ¼fung fehlgeschlagen.';
    } else {
        // 2) Formulardaten holen und bereinigen
        $name      = $conn->real_escape_string(trim($_POST['name']      ?? ''));
        $vorname   = $conn->real_escape_string(trim($_POST['vorname']   ?? ''));
        $nickname  = $conn->real_escape_string(trim($_POST['nickname']  ?? ''));
        $phone     = $conn->real_escape_string(trim($_POST['phone']     ?? ''));
        $email     = $conn->real_escape_string(trim($_POST['email']     ?? ''));
        $kategorie = $conn->real_escape_string(trim($_POST['kategorie'] ?? ''));
        $gewicht   = (isset($_POST['gewicht']) && $_POST['gewicht'] !== '') ? (float)$_POST['gewicht'] : null;

        // Geburtsdatum verarbeiten (erforderlich)
        [$dob_ok, $geburtsdatum_or_err] = normalize_birthdate($_POST['geburtsdatum'] ?? '');
        if (!$dob_ok) {
            $error_message = $geburtsdatum_or_err;
        }

        // 3) Pflichtfelder prÃ¼fen
        if (empty($error_message) && ($name === '' || $vorname === '' || $kategorie === '')) {
            $error_message = "Bitte fÃ¼llen Sie alle Pflichtfelder aus.";
        }

        if (empty($error_message)) {
            $geburtsdatum = $geburtsdatum_or_err; // hier im Format YYYY-MM-DD

            // 4) SQL vorbereiten (Geburtsdatum jetzt inkludiert)
            $sql  = "INSERT INTO participant (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum, Gewicht) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)";
            $stmt = $conn->prepare($sql);
            if (!$stmt) {
                $error_message = "Datenbank-Fehler: " . htmlspecialchars($conn->error);
            } else {
                // 7x string (inkl. Geburtsdatum) + 1x double/NULL
                $stmt->bind_param("sssssssd", $name, $vorname, $nickname, $phone, $email, $kategorie, $geburtsdatum, $gewicht);

                if ($stmt->execute()) {
                    $startnummer = $stmt->insert_id;
                    $success_message  = "Registrierung erfolgreich!<br><br>";
                    $success_message .= "<strong>Startnummer:</strong> " . htmlspecialchars((string)$startnummer) . "<br>";
                    $success_message .= "<strong>Name:</strong> " . htmlspecialchars($name) . "<br>";
                    $success_message .= "<strong>Vorname:</strong> " . htmlspecialchars($vorname) . "<br>";
                    $success_message .= "<strong>Spitzname:</strong> " . htmlspecialchars($nickname) . "<br>";
                    $success_message .= "<strong>Telefon:</strong> " . htmlspecialchars($phone) . "<br>";
                    $success_message .= "<strong>E-Mail:</strong> " . htmlspecialchars($email) . "<br>";
                    $success_message .= "<strong>Kategorie:</strong> " . htmlspecialchars($kategorie) . "<br>";
                    $success_message .= "<strong>Geburtsdatum:</strong> " . htmlspecialchars($geburtsdatum) . "<br>";
                    if ($gewicht !== null) {
                        $success_message .= "<strong>Gewicht:</strong> " . htmlspecialchars((string)$gewicht) . " kg<br>";
                    }
                    $success_message .= "<strong>Registrierungsdatum:</strong> " . date('Y-m-d H:i:s');
                } else {
                    $error_message = "Fehler: " . htmlspecialchars($stmt->error);
                }
                $stmt->close();
            }
        }
    }
}

$conn->close();
?>
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>Teilnehmer-Registrierung</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <!-- reCAPTCHA v2 Client Script -->
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>

    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #000; }
        .container { max-width: 600px; margin: 0 auto; background: #000; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.4); }
        h1 { color: #d2d63d; text-align: center; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; color: #d2d63d; }
        input[type="text"],
        input[type="email"],
        input[type="number"],
        input[type="date"],
        select {
            width: 100%;
            padding: 10px;
            border: 1px solid #444;
            border-radius: 6px;
            box-sizing: border-box;
            background-color: #1c1c1c;
            color: #d2d63d;
        }
        /* Platzhalter & leere Option */
        input::placeholder,
        select option[value=""] { color: #a6a831; }
        input:focus, select:focus {
            outline: none; border-color: #d2d63d; box-shadow: 0 0 5px #d2d63d;
        }
        /* Date-Picker Pfeile/Icons in hell */
        input[type="date"]::-webkit-calendar-picker-indicator { filter: invert(1); }

        button[type="submit"], .back-link {
            display: inline-block;
            background: #d2d63d;
            color: #1c1c1c;
            border: none;
            padding: 10px 14px;
            border-radius: 6px;
            font-weight: bold;
            cursor: pointer;
            text-decoration: none;
        }
        button[type="submit"]:hover, .back-link:hover { filter: brightness(1.05); }
        .success, .error {
            background-color: #1c1c1c;
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .success { color: #d2d63d; border: 1px solid #4CAF50; }
        .error   { color: #ff6666; border: 1px solid #ff3333; }

        .grecaptcha-badge { z-index: 1000; }
    </style>
</head>
<body>
    <div class="container">

        <!-- Logo -->
        <div style="text-align:center; margin-bottom:20px;">
            <img src="mutterschiff.jpg" alt="Kinoklub Logo" style="max-width:180px; height:auto;">
        </div>

        <h1>Teilnehmer-Registrierung</h1>

        <?php if (!empty($success_message)): ?>
            <div class="success"><?php echo $success_message; ?></div>
            <a href="<?php echo htmlspecialchars($_SERVER['PHP_SELF']); ?>" class="back-link">Weiteren Teilnehmer registrieren</a>
        <?php elseif (!empty($error_message)): ?>
            <div class="error"><?php echo htmlspecialchars($error_message); ?></div>
        <?php endif; ?>

        <?php if (empty($success_message)): ?>
        <form method="POST" action="<?php echo htmlspecialchars($_SERVER['PHP_SELF']); ?>">
            <div class="form-group">
                <label for="vorname">Vorname: *</label>
                <input type="text" id="vorname" name="vorname" required>
            </div>

            <div class="form-group">
                <label for="name">Nachname: *</label>
                <input type="text" id="name" name="name" required>
            </div>

            <div class="form-group">
                <label for="nickname">Spitzname:</label>
                <input type="text" id="nickname" name="nickname" placeholder="Optional">
            </div>

            <div class="form-group">
                <label for="phone">Telefon:</label>
                <input type="text" id="phone" name="phone" placeholder="+41 ...">
            </div>

            <div class="form-group">
                <label for="email">E-Mail:</label>
                <input type="email" id="email" name="email" placeholder="name@beispiel.ch">
            </div>

            <div class="form-group">
                <label for="kategorie">Kategorie: *</label>
                <select id="kategorie" name="kategorie" required>
                    <option value="">Bitte auswÃ¤hlen</option>
                    <option value="Standard">Keine Ã„nderungen am Fahrzeug vorgenommen (Standard)</option>
                    <option value="Pimped">Ã„nderungen am Fahrzeug vorgenommen (Pimped)</option>
                </select>
            </div>

            <div class="form-group">
                <label for="geburtsdatum">Geburtsdatum: *</label>
                <!-- HTML5 Datepicker; erlaubt auch direkte Eingabe -->
                <input
                    type="date"
                    id="geburtsdatum"
                    name="geburtsdatum"
                    required
                    inputmode="numeric"
                    pattern="\d{4}-\d{2}-\d{2}"
                    placeholder="YYYY-MM-DD"
                    max="<?php echo date('Y-m-d'); ?>"
                    min="1900-01-01"
                >
                <small style="color:#a6a831;">
                    Tipp: Du kannst auch â€žDD.MM.YYYYâ€œ eingeben (z. B. 31.12.2000).
                </small>
            </div>

            <!-- Optional: Gewicht -->
            <div class="form-group">
                <label for="gewicht">Gewicht (kg):</label>
                <input type="number" step="0.1" id="gewicht" name="gewicht" placeholder="z. B. 72.5">
            </div>

            <!-- reCAPTCHA Widget im Dark-Mode -->
            <div class="form-group">
                <div class="g-recaptcha"
                     data-sitekey="<?php echo htmlspecialchars($RECAPTCHA_SITE_KEY); ?>"
                     data-theme="dark"
                     data-size="normal"></div>
            </div>

            <button type="submit">Teilnehmer registrieren</button>
        </form>
        <?php endif; ?>
    </div>
</body>
</html>
