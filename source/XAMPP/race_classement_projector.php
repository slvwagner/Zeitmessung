<?php
/****************************************************
 * Race Classement — PROJECTOR MODE (Fullscreen)
 * - Rank by mean of all completed runs (lower is better)
 * - Show Δ Leader (mean vs leader's mean)
 * - Show last 3 measured times with Δ to global best single time
 * - Big fonts, minimal UI, fullscreen toggle (key "F")
 * - Auto-refresh: ?refresh=5  | Limit rows: ?rows=20
 ****************************************************/

//////////////////////
// SETTINGS (GET)   //
//////////////////////
$refresh = max(0, intval($_GET['refresh'] ?? 5));  // seconds; 0 disables auto-refresh
$maxRows = max(0, intval($_GET['rows'] ?? 0));     // 0 = no limit

//////////////////////
// DB CREDENTIALS   //
//////////////////////
$DB_HOST = "localhost";
$DB_NAME = "zeitmessung_V2";
$DB_USER = "root";
$DB_PASS = "";
$DB_CHARSET = "utf8mb4";

//////////////////////
// UTIL FUNCTIONS   //
//////////////////////
function format_ms($ms_int) {
    if ($ms_int === null) return "";
    $hours = intdiv($ms_int, 3600000);
    $rem   = $ms_int % 3600000;
    $mins  = intdiv($rem, 60000);
    $rem   = $rem % 60000;
    $secs  = intdiv($rem, 1000);
    $mill  = $rem % 1000;
    return sprintf("%02d:%02d:%02d.%03d", $hours, $mins, $secs, $mill);
}
function format_delta($ms_int) {
    if ($ms_int === null) return "";
    if ($ms_int === 0) return "±0";
    $sign = $ms_int > 0 ? "+" : "";
    return $sign . format_ms(abs($ms_int));
}

//////////////////////
// DB CONNECTION    //
//////////////////////
$dsn = "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=$DB_CHARSET";
$options = [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
];
try {
    $pdo = new PDO($dsn, $DB_USER, $DB_PASS, $options);
} catch (Exception $e) {
    http_response_code(500);
    echo "<pre>DB connection failed: " . htmlspecialchars($e->getMessage()) . "</pre>";
    exit;
}

//////////////////////
// DATA QUERY       //
//////////////////////
$sql = <<<SQL
WITH durations AS (
  SELECT
    p.Startnummer,
    p.Name,
    p.Vorname,
    r.run,
    MIN(CASE WHEN r.race_status IN ('started','start')   THEN r.timestamp_ms END) AS start_time,
    MAX(CASE WHEN r.race_status IN ('finished','finish') THEN r.timestamp_ms END) AS finish_time
  FROM participant p
  JOIN race r ON r.Startnummer = p.Startnummer
  GROUP BY p.Startnummer, p.Name, p.Vorname, r.run
),
completed AS (
  SELECT
    Startnummer,
    Name,
    Vorname,
    `run`,
    start_time,
    finish_time,
    TIMESTAMPDIFF(MICROSECOND, start_time, finish_time) DIV 1000 AS duration_ms
  FROM durations
  WHERE start_time IS NOT NULL AND finish_time IS NOT NULL
)
SELECT
  Startnummer, Name, Vorname, `run`, finish_time, duration_ms
FROM completed
ORDER BY Startnummer, finish_time DESC
SQL;

$rows = $pdo->query($sql)->fetchAll();

//////////////////////
// BUILD MODEL      //
//////////////////////
$byRider = [];                  // Startnummer => data
$globalBestSingle = null;       // best single time in ms across all riders

foreach ($rows as $r) {
    $sn   = (int)$r['Startnummer'];
    $name = $r['Name'];
    $vor  = $r['Vorname'];
    $dur  = (int)$r['duration_ms'];
    $fin  = $r['finish_time'];

    if (!isset($byRider[$sn])) {
        $byRider[$sn] = [
            'Startnummer' => $sn,
            'Name'        => $name,
            'Vorname'     => $vor,
            'durations'   => [],    // most recent first (since query sorted finish_time DESC per rider)
            'last3'       => [],
            'runs'        => 0,
            'mean_ms'     => null,
            'best_ms'     => null,
        ];
    }
    $byRider[$sn]['durations'][] = $dur;
    $byRider[$sn]['runs']++;
    if ($globalBestSingle === null || $dur < $globalBestSingle) {
        $globalBestSingle = $dur;
    }
}

foreach ($byRider as &$r) {
    if ($r['runs'] > 0) {
        $sum = array_sum($r['durations']);
        $r['mean_ms'] = intdiv($sum, $r['runs']);
        $r['best_ms'] = min($r['durations']);
        $r['last3']   = array_slice($r['durations'], 0, 3);
    }
}
unset($r);

// Leader by mean
$leaderMean = null;
foreach ($byRider as $r) {
    if ($r['mean_ms'] !== null) {
        $leaderMean = ($leaderMean === null) ? $r['mean_ms'] : min($leaderMean, $r['mean_ms']);
    }
}

// Classement: sort riders by mean asc, then best single asc, then Startnummer
$classement = array_values($byRider);
usort($classement, function($a, $b) {
    if ($a['mean_ms'] === null && $b['mean_ms'] === null) return $a['Startnummer'] <=> $b['Startnummer'];
    if ($a['mean_ms'] === null) return 1;
    if ($b['mean_ms'] === null) return -1;
    if ($a['mean_ms'] !== $b['mean_ms']) return $a['mean_ms'] <=> $b['mean_ms'];
    if ($a['best_ms'] !== $b['best_ms']) return $a['best_ms'] <=> $b['best_ms'];
    return $a['Startnummer'] <=> $b['Startnummer'];
});
if ($maxRows > 0) {
    $classement = array_slice($classement, 0, $maxRows);
}
?>
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Klassement – Projektor</title>
  <?php if ($refresh > 0): ?>
  <meta http-equiv="refresh" content="<?= htmlspecialchars($refresh) ?>">
  <?php endif; ?>
  <style>
    :root {
      --bg: #0b1020;       /* dark navy */
      --card: #0f172a;     /* slate-900 */
      --muted: #93a4be;    /* desaturated blue-gray */
      --fg: #e6ebf4;       /* off-white */
      --accent: #22d3ee;   /* cyan-400 */
      --good: #22c55e;     /* green-500 */
      --warn: #f59e0b;     /* amber-500 */
      --border: #1f2a40;   /* slate-800 */
      --row-alt: #111a30;  /* row zebra */
    }
    html, body { height: 100%; }
    body {
      margin: 0; background: var(--bg); color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial;
    }
    .wrap {
      max-width: 1600px; margin: 0 auto; padding: 1.2vw 1.6vw 2vw;
    }
    .hdr {
      display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
      margin-bottom: 0.5vw;
    }
    h1 {
      font-size: clamp(24px, 3.2vw, 54px); margin: 0; letter-spacing: 0.4px;
    }
    .sub {
      color: var(--muted);
      font-size: clamp(12px, 1.2vw, 20px);
      margin-bottom: 0.8vw;
    }
    .btn {
      border: 1px solid var(--border); background: transparent; color: var(--fg);
      border-radius: 999px; padding: 0.4em 0.9em; cursor: pointer;
      font-size: clamp(12px, 1.2vw, 18px);
    }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 1.2vw; padding: 0.6vw 0.6vw 1vw;
      box-shadow: 0 10px 20px rgba(0,0,0,0.25);
    }
    .table { width: 100%; border-collapse: collapse; }
    .table thead th {
      position: sticky; top: 0; background: var(--card);
      border-bottom: 1px solid var(--border);
      font-weight: 700; text-align: left; padding: 0.7vw 0.6vw;
      font-size: clamp(14px, 1.4vw, 26px);
      letter-spacing: 0.3px;
    }
    .table tbody td {
      border-bottom: 1px solid var(--border);
      padding: 0.7vw 0.6vw; font-size: clamp(16px, 1.8vw, 34px);
      vertical-align: middle; font-variant-numeric: tabular-nums;
    }
    .table tbody tr:nth-child(even) { background: var(--row-alt); }
    .rank  { width: 5ch; text-align: right; padding-right: 1.2vw !important; }
    .sn    { width: 7ch; }
    .name  { min-width: 20ch; }
    .runs  { width: 6ch; text-align: right; }
    .mean, .dlead { white-space: nowrap; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.4vw; }
    .chip  {
      display: inline-block; padding: 0.2vw 0.6vw; border-radius: 999px;
      border: 1px solid var(--border); font-size: clamp(12px, 1.3vw, 24px);
    }
    .delta-better { color: var(--good); }
    .delta-worse  { color: var(--warn); }
    .legend { color: var(--muted); font-size: clamp(12px, 1.1vw, 20px); }
    @media (max-width: 900px) {
      .runs, .dlead { display:none; }
      .table thead th:nth-child(4) { display:none; }
      .table thead th:nth-child(6) { display:none; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr">
      <h1>Klassement – Projektor (Mittelwert aller Läufe)</h1>
      <div>
        <button class="btn" id="fsBtn" title="Taste F: Vollbild">Vollbild</button>
      </div>
    </div>
    <div class="sub">
      <span class="legend">Δ Leader = Differenz zum Führenden (Mittelwert). Letzte 3 Zeiten: Δ zur besten Einzelzeit insgesamt.</span><br/>
      <?php
        $now = new DateTime("now", new DateTimeZone("Europe/Zurich"));
        $refStr = $refresh > 0 ? " – Auto-Refresh: {$refresh}s" : "";
        echo "Aktualisiert: " . $now->format("Y-m-d H:i:s") . $refStr;
      ?>
    </div>

    <div class="card">
      <table class="table">
        <thead>
          <tr>
            <th class="rank">Rk</th>
            <th class="sn">Startnr.</th>
            <th class="name">Teilnehmer</th>
            <th class="runs">Läufe</th>
            <th class="mean">Mittel</th>
            <th class="dlead">Δ Leader</th>
            <th>Letzte 3 (Δ → Bestzeit)</th>
          </tr>
        </thead>
        <tbody>
          <?php
          $rank = 0;
          foreach ($classement as $r) {
              if ($r['mean_ms'] === null) continue; // no completed runs
              $rank++;
              $deltaLeader = ($leaderMean !== null) ? ($r['mean_ms'] - $leaderMean) : null;

              $last3Html = "";
              if (!empty($r['last3'])) {
                  $chips = [];
                  foreach ($r['last3'] as $dms) {
                      $deltaBest = ($globalBestSingle !== null) ? ($dms - $globalBestSingle) : null;
                      $cls = ($deltaBest > 0) ? "delta-worse" : (($deltaBest < 0) ? "delta-better" : "");
                      $deltaStr = ($deltaBest === null || $deltaBest === 0) ? "±0" : format_delta($deltaBest);
                      $chips[] = "<span class='chip $cls'>".format_ms($dms)." ($deltaStr)</span>";
                  }
                  $last3Html = "<div class='chips'>".implode("", $chips)."</div>";
              } else {
                  $last3Html = "<span class='legend'>–</span>";
              }

              $nameFull = htmlspecialchars($r['Name'] . " " . $r['Vorname']);
              echo "<tr>";
              echo "<td class='rank'>".$rank."</td>";
              echo "<td class='sn'>".$r['Startnummer']."</td>";
              echo "<td class='name'>".$nameFull."</td>";
              echo "<td class='runs'>".$r['runs']."</td>";
              echo "<td class='mean'><strong>".format_ms($r['mean_ms'])."</strong></td>";

              if ($deltaLeader === null || $deltaLeader === 0) {
                  echo "<td class='dlead'><span class='delta-better'>±0</span></td>";
              } else {
                  $cls = $deltaLeader > 0 ? "delta-worse" : "delta-better";
                  echo "<td class='dlead'><span class='$cls'>".format_delta($deltaLeader)."</span></td>";
              }

              echo "<td>".$last3Html."</td>";
              echo "</tr>";
          }

          // Optional: show riders without a completed run (at bottom)
          $noRuns = array_filter($classement, fn($x) => $x['mean_ms'] === null);
          if (!empty($noRuns)) {
              echo "<tr><td colspan='7' class='legend' style='padding-top:1vw;'>Teilnehmer ohne gewerteten Lauf:</td></tr>";
              foreach ($noRuns as $r) {
                  $nameFull = htmlspecialchars($r['Name'] . " " . $r['Vorname']);
                  echo "<tr>";
                  echo "<td class='rank'>–</td>";
                  echo "<td class='sn'>".$r['Startnummer']."</td>";
                  echo "<td class='name'>".$nameFull."</td>";
                  echo "<td class='runs'>0</td>";
                  echo "<td class='mean'>–</td>";
                  echo "<td class='dlead'>–</td>";
                  echo "<td>–</td>";
                  echo "</tr>";
              }
          }
          ?>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    // Fullscreen helper (button + "F" key)
    const fsBtn = document.getElementById('fsBtn');
    function toggleFullscreen() {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    }
    fsBtn?.addEventListener('click', toggleFullscreen);
    document.addEventListener('keydown', (e) => {
      if (e.key.toLowerCase() === 'f') toggleFullscreen();
    });
  </script>
</body>
</html>

