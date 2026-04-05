// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
/****************************************************
 * Race Classement (Mean over all runs)
 * - Leader determined by lowest mean time
 * - Show last 3 measured times with Δ to global best single
 * - Show Δ Leader (difference to leader's mean)
 * - Mobile-friendly + auto-refresh
 ****************************************************/

//////////////////////
// DB CREDENTIALS  //
//////////////////////
$DB_HOST = "localhost";
$DB_NAME = "zeitmessung";
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
    $sign = $ms_int > 0 ? "+" : "";
    return $sign . format_ms($ms_int);
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
/*
 We’ll compute completed run durations directly from the event-log,
 so we have numeric milliseconds for means and deltas.
*/
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
            'durations'   => [],    // list of ms, ordered by finish_time desc in input
            'last3'       => [],    // last 3 durations (ms)
            'runs'        => 0,
            'mean_ms'     => null,
            'best_ms'     => null,
            'last_finish' => null,
        ];
    }
    $byRider[$sn]['durations'][] = $dur;
    $byRider[$sn]['runs']++;
    $byRider[$sn]['last_finish'] = $byRider[$sn]['last_finish'] ?? $fin;

    if ($globalBestSingle === null || $dur < $globalBestSingle) {
        $globalBestSingle = $dur;
    }
}

// compute aggregates per rider
foreach ($byRider as &$r) {
    if ($r['runs'] > 0) {
        $sum = array_sum($r['durations']);
        $r['mean_ms'] = intdiv($sum, $r['runs']); // integer mean
        $r['best_ms'] = min($r['durations']);
        // last 3 by *latest measured* → input was ordered by finish_time DESC within rider
        $r['last3'] = array_slice($r['durations'], 0, 3);
    }
}
unset($r);

// leader by mean
$leaderMean = null;
foreach ($byRider as $r) {
    if ($r['mean_ms'] !== null) {
        $leaderMean = ($leaderMean === null) ? $r['mean_ms'] : min($leaderMean, $r['mean_ms']);
    }
}

// turn map into sorted list (rank by mean asc; then best single asc; then Startnummer)
$classement = array_values($byRider);
usort($classement, function($a, $b) {
    // Riders without valid runs go to bottom
    if ($a['mean_ms'] === null && $b['mean_ms'] === null) {
        return $a['Startnummer'] <=> $b['Startnummer'];
    } elseif ($a['mean_ms'] === null) {
        return 1;
    } elseif ($b['mean_ms'] === null) {
        return -1;
    }
    if ($a['mean_ms'] !== $b['mean_ms']) return $a['mean_ms'] <=> $b['mean_ms'];
    if ($a['best_ms'] !== $b['best_ms']) return $a['best_ms'] <=> $b['best_ms'];
    return $a['Startnummer'] <=> $b['Startnummer'];
});
?>
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Klassement – Mittelwert aller Läufe</title>
  <meta http-equiv="refresh" content="5"> <!-- auto-refresh every 5s -->

  <style>
    :root {
      --bg: #0f172a;       /* slate-900 */
      --card: #111827;     /* gray-900 */
      --muted: #94a3b8;    /* slate-400 */
      --fg: #e5e7eb;       /* gray-200 */
      --accent: #22d3ee;   /* cyan-400 */
      --good: #22c55e;     /* green-500 */
      --warn: #f59e0b;     /* amber-500 */
      --bad: #ef4444;      /* red-500 */
      --border: #1f2937;   /* gray-800 */
    }
    html, body { height: 100%; }
    body {
      margin: 0; background: var(--bg); color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }
    .wrap {
      max-width: 1200px; margin: 0 auto; padding: 16px;
    }
    h1 {
      font-size: 1.6rem; margin: 0 0 8px 0; letter-spacing: 0.2px;
    }
    .sub {
      color: var(--muted); font-size: 0.95rem; margin-bottom: 14px;
    }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 16px; padding: 8px 8px 12px 8px;
      box-shadow: 0 10px 20px rgba(0,0,0,0.25);
    }
    .table {
      width: 100%; border-collapse: collapse;
    }
    .table thead th {
      position: sticky; top: 0; background: var(--card);
      border-bottom: 1px solid var(--border);
      font-weight: 600; text-align: left; padding: 10px 8px; font-size: 0.9rem;
    }
    .table tbody td {
      border-bottom: 1px solid var(--border);
      padding: 10px 8px; font-size: 0.95rem; vertical-align: top;
    }
    .rank { width: 44px; text-align: right; padding-right: 12px !important; }
    .sn { width: 72px; font-variant-numeric: tabular-nums; }
    .name { min-width: 160px; }
    .runs, .mean, .dlead { white-space: nowrap; font-variant-numeric: tabular-nums; }
    .last3 { font-variant-numeric: tabular-nums; }
    .chip {
      display: inline-block; padding: 4px 6px; border-radius: 999px;
      border: 1px solid var(--border); margin-right: 6px; margin-bottom: 6px;
      font-size: 0.9rem;
    }
    .delta-better { color: var(--good); }
    .delta-worse { color: var(--warn); }
    .hdr {
      display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
      flex-wrap: wrap;
    }
    .legend { color: var(--muted); font-size: 0.9rem; }
    @media (max-width: 720px) {
      .name { min-width: 120px; }
      .runs { display:none; }
      .dlead { display:none; } /* hide Δ Leader on very small screens to save space */
      .table thead th:nth-child(4) { display:none; } /* header for runs */
      .table thead th:nth-child(6) { display:none; } /* header for Δ Leader */
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr">
      <h1>Aktuelles Klassement – Mittelwert aller Läufe</h1>
      <div class="legend">Δ Leader = Differenz zum Führenden (Mittelwert). Letzte 3 Zeiten: Δ zur besten Einzelzeit insgesamt.</div>
    </div>
    <div class="sub">
      <?php
        $now = new DateTime("now", new DateTimeZone("Europe/Zurich"));
        echo "Aktualisiert: " . $now->format("Y-m-d H:i:s");
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
            <th class="last3">Letzte 3 Zeiten (Δ zur Bestzeit)</th>
          </tr>
        </thead>
        <tbody>
          <?php
          $rank = 0;
          foreach ($classement as $r) {
              if ($r['mean_ms'] === null) continue; // skip riders without a completed run
              $rank++;

              $deltaLeader = ($leaderMean !== null) ? ($r['mean_ms'] - $leaderMean) : null;

              // build last 3 label: "00:01:23.456 (+0:00:00.789) • ..."
              $last3Labels = [];
              foreach ($r['last3'] as $dms) {
                  $deltaBest = ($globalBestSingle !== null) ? ($dms - $globalBestSingle) : null;
                  $deltaStr  = ($deltaBest !== null && $deltaBest !== 0) ? " (".format_delta($deltaBest).")" : " (±0)";
                  $last3Labels[] = "<span class='chip'>".format_ms($dms).$deltaStr."</span>";
              }
              $last3Html = implode("", $last3Labels);

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
              echo "<td class='last3'>".$last3Html."</td>";
              echo "</tr>";
          }

          // Optionally show riders with no completed run (bottom list)
          $noRuns = array_filter($classement, fn($x) => $x['mean_ms'] === null);
          if (!empty($noRuns)) {
              echo "<tr><td colspan='7' style='color: var(--muted); padding-top:14px;'>Teilnehmer ohne gewerteten Lauf:</td></tr>";
              foreach ($noRuns as $r) {
                  $nameFull = htmlspecialchars($r['Name'] . " " . $r['Vorname']);
                  echo "<tr>";
                  echo "<td class='rank'>–</td>";
                  echo "<td class='sn'>".$r['Startnummer']."</td>";
                  echo "<td class='name'>".$nameFull."</td>";
                  echo "<td class='runs'>0</td>";
                  echo "<td class='mean'>–</td>";
                  echo "<td class='dlead'>–</td>";
                  echo "<td class='last3'>–</td>";
                  echo "</tr>";
              }
          }
          ?>
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
