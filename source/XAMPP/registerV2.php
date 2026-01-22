<?php
// Immer UTF-8-Header senden (hilft mit Umlauten usw.)
header('Content-Type: text/html; charset=UTF-8');

// === reCAPTCHA-Schlüssel ===
$RECAPTCHA_SITE_KEY   = '6LdM8Q8sAAAAAAXk0DwtyC3GHMo783RGl2kJ8tCX';
$RECAPTCHA_SECRET_KEY = '6LdM8Q8sAAAAAGpvOmHr2D45cE-_WFmzqIiEuZ1s';

// Datenbank-Verbindungsdaten
$servername = "";
$username   = "";
$password   = "";
$dbname     = "";

// Verbindung herstellen
$conn = new mysqli($servername, $username, $password, $dbname);

// Verbindung prüfen
if ($conn->connect_error) {
    die("Verbindung fehlgeschlagen: " . htmlspecialchars($conn->connect_error));
}

// Zeichensatz auf utf8mb4 setzen (für Umlaute etc.)
$conn->set_charset("utf8mb4");

// Helfer: reCAPTCHA serverseitig prüfen
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
            return [false, 'reCAPTCHA-Überprüfung fehlgeschlagen (cURL): ' . $err];
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
            return [false, 'reCAPTCHA-Überprüfung fehlgeschlagen (HTTP-Anfrage).'];
        }
    }

    $json = json_decode($result, true);
    if (!is_array($json)) {
        return [false, 'Ungültige reCAPTCHA-Antwort von Google.'];
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
            return [false, 'Ungültiges Datum.'];
        }
    } else {
        // Ersetze / durch . und erwarte dann DD.MM.YYYY
        $tmp = str_replace('/', '.', $raw);
        if (preg_match('/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/', $tmp, $m)) {
            $d = (int)$m[1];
            $mth = (int)$m[2];
            $y = (int)$m[3];
            if (!checkdate($mth, $d, $y)) {
                return [false, 'Ungültiges Datum.'];
            }
            $iso = sprintf('%04d-%02d-%02d', $y, $mth, $d);
        } else {
            return [false, 'Bitte Datum als YYYY-MM-DD oder DD.MM.YYYY eingeben.'];
        }
    }

    // Logische Prüfung: nicht in der Zukunft, realistisch alt
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
    // 1) Disclaimer prüfen
    $disclaimer_accepted = isset($_POST['disclaimer']) && $_POST['disclaimer'] === '1';
    if (!$disclaimer_accepted) {
        $error_message = "Sie müssen den Haftungsausschluss und die Einverständniserklärung akzeptieren, um fortzufahren.";
    } else {
        // 2) reCAPTCHA prüfen
        [$ok, $recaptcha_err] = verify_recaptcha(
            $RECAPTCHA_SECRET_KEY,
            $_POST['g-recaptcha-response'] ?? '',
            $_SERVER['REMOTE_ADDR'] ?? null
        );

        if (!$ok) {
            $error_message = $recaptcha_err ?: 'reCAPTCHA-Überprüfung fehlgeschlagen.';
        } else {
            // 3) Formulardaten holen und bereinigen
            $name      = $conn->real_escape_string(trim($_POST['name']      ?? ''));
            $vorname   = $conn->real_escape_string(trim($_POST['vorname']   ?? ''));
            $nickname  = $conn->real_escape_string(trim($_POST['nickname']  ?? ''));
            $phone     = $conn->real_escape_string(trim($_POST['phone']     ?? ''));
            // E-Mail: erst validieren, dann escapen
            $email_raw = trim($_POST['email'] ?? '');
            if ($email_raw !== '' && !filter_var($email_raw, FILTER_VALIDATE_EMAIL)) {
                $error_message = "Bitte eine gültige E-Mail-Adresse eingeben.";
            }
            $email     = $conn->real_escape_string($email_raw);
            $kategorie = $conn->real_escape_string(trim($_POST['kategorie'] ?? ''));
            $gewicht   = (isset($_POST['gewicht']) && $_POST['gewicht'] !== '') ? (float)$_POST['gewicht'] : null;

            // Geburtsdatum verarbeiten (erforderlich)
            [$dob_ok, $geburtsdatum_or_err] = normalize_birthdate($_POST['geburtsdatum'] ?? '');
            if (!$dob_ok) {
                $error_message = $geburtsdatum_or_err;
            }

            // 4) Pflichtfelder prüfen
            if (empty($error_message) && ($name === '' || $vorname === '' || $kategorie === '')) {
                $error_message = "Bitte füllen Sie alle Pflichtfelder aus.";
            }

            if (empty($error_message)) {
                $geburtsdatum = $geburtsdatum_or_err; // hier im Format YYYY-MM-DD

                // 5) SQL vorbereiten (Geburtsdatum jetzt inkludiert)
                $sql  = "INSERT INTO participants (Name, Vorname, Nickname, Phone, `E-mail`, Kategorie, Geburtsdatum, Gewicht) 
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)";
                $stmt = $conn->prepare($sql);
                if (!$stmt) {
                    $error_message = "Datenbank-Fehler: " . htmlspecialchars($conn->error);
                } else {
                    // 7x string (inkl. Geburtsdatum) + 1x double/NULL
                    $stmt->bind_param("sssssssd", $name, $vorname, $nickname, $phone, $email, $kategorie, $geburtsdatum, $gewicht);

                    if ($stmt->execute()) {
                        $Registrierungsnummer = $stmt->insert_id;
                        $success_message  = "Registrierung erfolgreich!<br><br>";
                        $success_message .= "<strong>Registrierungsnummer:</strong> " . htmlspecialchars((string)$Registrierungsnummer) . "<br>";
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
}

$conn->close();
?>

<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>Bobbycar race</title>
    
    <!-- Favicon -->
    <link rel="icon" type="image/png" href="mutti.ico">
    
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <!-- reCAPTCHA v2 Client Script -->
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>

    <!-- Flatpickr CSS and JS -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/themes/dark.css">
    <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
    <script src="https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/de.js"></script>

    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #000; }
        .container { max-width: 600px; margin: 0 auto; background: #000; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.4); }
        h1 { color: #FFCD00; text-align: center; }
        h2 { color: #FFCD00; text-align: center; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; color: #FFCD00; }
        input[type="text"],
        input[type="email"],
        input[type="number"],
        select {
            width: 100%;
            padding: 10px;
            border: 1px solid #444;
            border-radius: 6px;
            box-sizing: border-box;
            background-color: #1c1c1c;
            color: #FFCD00;
        }
        /* Platzhalter & leere Option */
        input::placeholder,
        select option[value=""] { color: #a6a831; }
        input:focus, select:focus {
            outline: none; border-color: #FFCD00; box-shadow: 0 0 5px #FFCD00;
        }

        button[type="submit"], .back-link {
            display: inline-block;
            background: #FFCD00;
            color: #1c1c1c;
            border: none;
            padding: 10px 14px;
            border-radius: 6px;
            font-weight: bold;
            cursor: pointer;
            text-decoration: none;
            margin-top: 10px;
        }
        button[type="submit"]:hover, .back-link:hover { filter: brightness(1.05); }
        .success, .error {
            background-color: #1c1c1c;
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .success { color: #FFCD00; border: 1px solid #4CAF50; }
        .error   { color: #ff6666; border: 1px solid #ff3333; }

        .grecaptcha-badge { z-index: 1000; }

        /* Ensure the Flatpickr calendar floats above everything (incl. reCAPTCHA badge) */
        .flatpickr-calendar { z-index: 99999 !important; }
        
        /* Disclaimer styling */
        .disclaimer-box {
            background-color: #1c1c1c;
            border: 1px solid #444;
            border-radius: 6px;
            padding: 15px;
            margin-bottom: 20px;
            max-height: 300px;
            overflow-y: auto;
            color: #FFCD00;
            font-size: 14px;
            line-height: 1.5;
        }
        
        .disclaimer-box h3 {
            color: #FFCD00;
            margin-top: 0;
            border-bottom: 1px solid #444;
            padding-bottom: 10px;
        }
        
        .disclaimer-checkbox {
            margin-top: 15px;
            padding: 10px;
            background-color: #1c1c1c;
            border-radius: 6px;
            border: 1px solid #444;
        }
        
        .disclaimer-checkbox label {
            display: flex;
            align-items: flex-start;
            color: #FFCD00;
            cursor: pointer;
            font-weight: normal;
        }
        
        .disclaimer-checkbox input[type="checkbox"] {
            margin-right: 10px;
            margin-top: 3px;
            flex-shrink: 0;
        }
        
        .disclaimer-checkbox strong {
            color: #ff6666;
        }
    </style>
</head>
<body>
    <div class="container">

        <!-- Logo -->
        <div style="text-align:center; margin-bottom:20px;">
            <img src="Logo Bobbycar Version 2.png" alt="Kinoklub Logo" style="max-width:180px; height:auto;">
        </div>

        <h1>Mutti kratzt die Kurve</h1>
        <h2>Teilnehmer-Registrierung</h2>
        <h2>Bobby-Car-Rennen</h2>

        <?php if (!empty($success_message)): ?>
            <div class="success"><?php echo $success_message; ?></div>
            <a href="<?php echo htmlspecialchars($_SERVER['PHP_SELF']); ?>" class="back-link">Weiteren Teilnehmer registrieren</a>
        <?php elseif (!empty($error_message)): ?>
            <div class="error"><?php echo htmlspecialchars($error_message); ?></div>
        <?php endif; ?>

        <?php if (empty($success_message)): ?>
        <form method="POST" action="<?php echo htmlspecialchars($_SERVER['PHP_SELF']); ?>" id="registrationForm">
            
            <!-- Disclaimer Section -->
            <div class="form-group">
                <div class="disclaimer-box">
                    <hr>
                    <a href="https://docs.google.com/document/d/1aOO5m7Nd-zLnh9X1jMlN8P0nXxK-uLlPZoUs0sfdIKw/edit?usp=sharing" target="_blank" style="font-size: 24px; color: white">Rennreglement</a>
                    <hr>
                    <div style="font-size: 32px; color: red; font-weight: bold;">Haftungsausschluss und Einverständniserklärung</div>
                    <p>Ich bin mir bewusst, dass das BobbyCar-Rennen grundsätzlich Gefahren und Risiken bergen kann. Meine Teilnahme erfolgt auf eigene Gefahr. Um mögliche Verletzungen zu vermeiden, ist mir das Reglement bekannt gemacht worden. Ich akzeptiere dieses als verbindlich und werde allen diesbezüglichen Anweisungen der Organisatoren Folge leisten.</p>
                    <p>Ich bin mir bewusst, dass die Organisatoren keine Haftung übernehmen bei Unfällen oder Verlust oder Beschädigungen jeglicher Art.</p>
                    <p>Ich bin mir bewusst, dass die Organisatoren nicht haften für höhere Gewalt und Zufall, sowie für Mängel, die auch bei Einhaltung der üblichen Sorgfalt nicht sofort erkannt werden.</p>
                    <p>Ich erkläre, dass ich für die von mir verschuldeten Beschädigungen an Gegenständen oder Dritten oder der Gesundheit Dritter hafte. Diese Haftung gilt auch für Kinder oder von mir zu beaufsichtigenden Personen.</p>
                    <p>Ich bin damit einverstanden, dass Fotos von mir auf der Homepage des Verein Mutterschiff publiziert werden.</p>
                    <p>Soweit ich nicht alleine erziehungs-berechtigt für die hier angegebenen Kinder bin, bestätige ich, bevollmächtigt für den Abschluss dieser Haftungserklärung zu sein.</p>
                </div>
                
                <div class="disclaimer-checkbox">
                    <label>
                        <input type="checkbox" name="disclaimer" value="1" required>
                        <span><strong>Ich habe das Rennreglement,  Haftungsausschluss und die Einverständniserklärung gelesen und akzeptiere diese vollständig.*</strong></span>
                    </label>
                </div>
            </div>

            <div class="form-group">
                <label for="vorname">Vorname: *</label>
                <input type="text" id="vorname" name="vorname" autocomplete="given-name" required>
            </div>

            <div class="form-group">
                <label for="name">Nachname: *</label>
                <input type="text" id="name" name="name" autocomplete="family-name" required>
            </div>

            <div class="form-group">
                <label for="nickname">Spitzname:</label>
                <input type="text" id="nickname" name="nickname" placeholder="Optional">
            </div>

            <div class="form-group">
                <label for="phone">Telefon:</label>
                <input type="text" id="phone" name="phone" placeholder="+41 ..." autocomplete="tel">
            </div>

            <div class="form-group">
                <label for="email">E-Mail:</label>
                <input type="email" id="email" name="email" placeholder="name@beispiel.ch" autocomplete="email">
            </div>

            <div class="form-group">
                <label for="kategorie">Kategorie: *</label>
                <select id="kategorie" name="kategorie" required>
                    <option value="">Bitte auswählen</option>
                    <option value="Standard">Keine Änderungen am Fahrzeug vorgenommen (Standard)</option>
                    <option value="Pimped">Änderungen am Fahrzeug vorgenommen (Pimped)</option>
                </select>
            </div>

            <div class="form-group">
                <label for="geburtsdatum">Geburtsdatum: *</label>
                <input
                    type="text"
                    id="geburtsdatum"
                    name="geburtsdatum"
                    required
                    inputmode="numeric"
                    placeholder="TT.MM.JJJJ"
                    autocomplete="bday"
                >
                <small style="color:#a6a831;">
                    Tipp: Du kannst das Datum direkt als TT.MM.JJJJ eingeben (z. B. 31.12.2012).
                </small>
            </div>
            
            <div class="form-group">
                <label for="gewicht">Gewicht (kg):</label>
                <input type="number" id="gewicht" name="gewicht" min="0" step="0.1" placeholder="Optional">
            </div>
            
            <!-- reCAPTCHA Widget im Dark-Mode -->
            <div class="form-group">
                <div class="g-recaptcha"
                     data-sitekey="<?php echo htmlspecialchars($RECAPTCHA_SITE_KEY); ?>"
                     data-theme="dark"
                     data-size="normal"></div>
            </div>

            <button type="submit" id="submitButton">Teilnehmer registrieren</button>
        </form>
        <?php endif; ?>
    </div>

    <script>
    document.addEventListener('DOMContentLoaded', function() {
        // Check if flatpickr is available
        if (typeof flatpickr !== 'undefined') {
            try {
                // Create date objects for min and max dates
                var today = new Date();
                var threeYearsAgo = new Date();
                threeYearsAgo.setFullYear(today.getFullYear() - 3);
                
                var minDate = new Date('1940-01-01');
                
                flatpickr('#geburtsdatum', {
                    dateFormat: 'd.m.Y',
                    allowInput: true,
                    locale: 'de',
                    minDate: minDate,
                    maxDate: threeYearsAgo,
                    defaultDate: new Date('2015-01-01'), // Default to a reasonable date
                    onChange: function(selectedDates, dateStr, instance) {
                        console.log('Selected date:', dateStr);
                    }
                });
                
                console.log('Date picker initialized with date range:', 
                    minDate.toLocaleDateString('de-DE'), 'to', 
                    threeYearsAgo.toLocaleDateString('de-DE'));
            } catch (e) {
                console.error('Error initializing date picker:', e);
            }
        } else {
            console.error('Flatpickr not loaded');
            // Fallback: Add a basic HTML5 date input if Flatpickr fails
            var dateInput = document.getElementById('geburtsdatum');
            if (dateInput) {
                var today = new Date();
                var threeYearsAgo = new Date();
                threeYearsAgo.setFullYear(today.getFullYear() - 3);
                
                dateInput.type = 'date';
                dateInput.min = '1900-01-01';
                dateInput.max = threeYearsAgo.toISOString().split('T')[0];
            }
        }
        
        // Form validation to ensure disclaimer is checked
        const form = document.getElementById('registrationForm');
        const disclaimerCheckbox = document.querySelector('input[name="disclaimer"]');
        
        if (form) {
            form.addEventListener('submit', function(e) {
                if (!disclaimerCheckbox.checked) {
                    e.preventDefault();
                    alert('Bitte akzeptieren Sie den Haftungsausschluss und die Einverständniserklärung, um fortzufahren.');
                    disclaimerCheckbox.focus();
                }
            });
        }
    });
    </script>
</body>
</html>

