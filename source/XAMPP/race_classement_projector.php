<?php
/****************************************************
 * Race Classement — PROJECTOR MODE (Fullscreen) + Kategorie Filter
 * NO META REFRESH (keeps fullscreen). Uses AJAX polling instead.
 * - Rank by mean of all completed runs (lower is better)
 * - Δ Leader (mean vs leader)
 * - Last 3 measured times (Δ to global best single time)
 * - Big fonts, minimal UI, fullscreen toggle (key "F")
 * - Polling interval: ?refresh=5  | Row limit: ?rows=20 | Kategorie: ?cat=Pimped
 * - If called with ?ajax=1 returns JSON payload for client updates
 ****************************************************/

//////////////////////
// SETTINGS (GET)   //
//////////////////////
$refresh = max(0, intval($_GET['refresh'] ?? 5));  // seconds; 0 disables auto-poll (but page still loads)
$maxRows = max(0, intval($_GET['rows'] ?? 0));     // 0 = no limit
$cat     = trim($_GET['cat'] ?? "");               // Kategorie filter ("" or "all" = none)
$isAjax  = isset($_GET['ajax']) && $_GET['ajax'] == '1';

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
function fmt_ms($ms_int) {
    if ($ms_int === null) return "";
    $hours = intdiv($ms_int, 3600000);
    $rem   = $ms_int % 3600000;
    $mins  = intdiv($rem, 60000);
    $rem   = $rem % 60000;
    $secs  = intdiv($rem, 1000);
    $mill  = $rem % 1000;
    return sprintf("%02d:%02d:%02d.%03d", $hours, $mins, $secs, $mill);
}
function fmt_delta($ms_int) {
    if ($ms_int === null) return "";
    if ($ms_int === 0) return "±0";
    $sign = $ms_int > 0 ? "+" : "";
    $abs  = abs($ms_int);
    return $sign . fmt_ms($abs);
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
    if ($isAjax) {
        http_response_code(500);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['error' => 'DB connection failed']);
        exit;
    }
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
    p.Kategorie,
    r.run,
    MIN(CASE WHEN r.race_status IN ('started','start')   THEN r.timestamp_ms END) AS start_time,
    MAX(CASE WHEN r.race_status IN ('finished','finish') THEN r.timestamp_ms END) AS finish_time
  FROM participant p
  JOIN race r ON r.Startnummer = p.Startnummer
  /**WHERE_CLAUSE**/
  GROUP BY p.Startnummer, p.Name, p.Vorname, p.Kategorie, r.run
),
completed AS (
  SELECT
    Startnummer,
    Name,
    Vorname,
    Kategorie,
    `run`,
    start_time,
    finish_time,
    TIMESTAMPDIFF(MICROSECOND, start_time, finish_time) DIV 1000 AS duration_ms
  FROM durations
  WHERE start_time IS NOT NULL AND finish_time IS NOT NULL
)
SELECT
  Startnummer, Name, Vorname, Kategorie, `run`, finish_time, duration_ms
FROM completed
ORDER BY Startnummer, finish_time DESC
SQL;

$params = [];
$where = "";
if ($cat !== "" && strtolower($cat) !== "all") {
    $where = "WHERE p.Kategorie = :cat";
    $params[':cat'] = $cat;
}
$sql = str_replace("/**WHERE_CLAUSE**/", $where, $sql);

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

//////////////////////
// BUILD MODEL      //
//////////////////////
$byRider = [];            // Startnummer => aggregates
$globalBestSingle = null; // best single time (ms) across filtered set

foreach ($rows as $r) {
    $sn   = (int)$r['Startnummer'];
    $name = $r['Name'];
    $vor  = $r['Vorname'];
    $kat  = $r['Kategorie'];
    $dur  = (int)$r['duration_ms'];

    if (!isset($byRider[$sn])) {
        $byRider[$sn] = [
            'Startnummer' => $sn,
            'Name'        => $name,
            'Vorname'     => $vor,
            'Kategorie'   => $kat,
            'durations'   => [],    // most recent first (input sorted by finish_time DESC per rider)
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
    } else {
        $r['last3'] = [];
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

//////////////////////
// AJAX RESPONSE    //
//////////////////////
if ($isAjax) {
    header('Content-Type: application/json; charset=utf-8');
    $payload = [
        'updated_at' => (new DateTime("now", new DateTimeZone("Europe/Zurich")))->format("Y-m-d H:i:s"),
        'category'   => ($cat !== "" && strtolower($cat) !== "all") ? $cat : "Alle Kategorien",
        'leaderMean' => $leaderMean,
        'globalBestSingle' => $globalBestSingle,
        'rows' => array_map(function($r) use ($leaderMean, $globalBestSingle) {
            $deltaLeader = ($leaderMean !== null && $r['mean_ms'] !== null) ? ($r['mean_ms'] - $leaderMean) : null;
            return [
                'Startnummer' => $r['Startnummer'],
                'Name'        => $r['Name'],
                'Vorname'     => $r['Vorname'],
                'runs'        => $r['runs'],
                'mean_ms'     => $r['mean_ms'],
                'mean_str'    => $r['mean_ms'] !== null ? fmt_ms($r['mean_ms']) : '–',
                'delta_leader_ms' => $deltaLeader,
                'delta_leader_str'=> ($deltaLeader === null) ? '–' : (($deltaLeader === 0) ? '±0' : fmt_delta($deltaLeader)),
                'last3'       => array_map(function($d) use ($globalBestSingle) {
                    $delta = ($globalBestSingle !== null) ? ($d - $globalBestSingle) : null;
                    return [
                        'time_ms' => $d,
                        'time_str'=> fmt_ms($d),
                        'delta_ms'=> $delta,
                        'delta_str'=> ($delta === null || $delta === 0) ? "±0" : fmt_delta($delta),
                        'delta_cls'=> ($delta === null || $delta === 0) ? "" : (($delta > 0) ? "delta-worse" : "delta-better"),
                    ];
                }, $r['last3']),
            ];
        }, $classement),
    ];
    echo json_encode($payload);
    exit;
}

//////////////////////
// NON-AJAX: PAGE   //
//////////////////////
$activeCatLabel = ($cat !== "" && strtolower($cat) !== "all") ? $cat : "Alle Kategorien";
?>
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Klassement – Projektor (<?= htmlspecialchars($activeCatLabel) ?>)</title>
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
    .wrap { max-width: 1600px; margin: 0 auto; padding: 1.2vw 1.6vw 2vw; }
    .hdr { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
    h1 { font-size: clamp(24px, 3.2vw, 54px); margin: 0; letter-spacing: 0.4px; }
    .sub { color: var(--muted); font-size: clamp(12px, 1.2vw, 20px); margin: 0.6vw 0 1vw; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 1.2vw; padding: 0.6vw 0.6vw 1vw; box-shadow: 0 10px 20px rgba(0,0,0,0.25); }
    .controls { display: flex; gap: 0.6vw; align-items: center; flex-wrap: wrap; }
    /* Controls */
    .select, .btn {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
      border-radius: 999px;
      padding: 0.35em 0.8em;
      font-size: clamp(12px, 1.1vw, 18px);
    }
    
    /* Make selects & number inputs dark (readable) */
    select.select,
    input.select {
      background-color: var(--row-alt);  /* dark background */
      color: var(--fg);                  /* light text */
    }
    
    /* Style the dropdown list items too */
    select.select option {
      background-color: var(--card);     /* dropdown panel bg */
      color: var(--fg);
    }
    
    /* Focus/hover states for accessibility */
    select.select:focus,
    input.select:focus {
      outline: 2px solid var(--accent);
      border-color: var(--accent);
    }
    
    /* Optional: subtler hover */
    select.select:hover,
    input.select:hover {
      border-color: var(--muted);
    }
    
    /* On Windows/Firefox, highlight selected option in list */
    select.select option:checked {
      background-color: var(--row-alt);
    }
    
    /* iOS Safari tweak: ensure text color inside picker */
    @supports (-webkit-touch-callout: none) {
      select.select {
        -webkit-text-fill-color: var(--fg);
      }
    }

    .select { min-width: 12ch; }
    .btn { cursor: pointer; }
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
      <h1>Klassement – Projektor (<span id="catLabel"><?= htmlspecialchars($activeCatLabel) ?></span>)</h1>
      <div class="controls">
        <form id="ctlForm" method="get" style="display:flex; gap:0.6vw; align-items:center;">
          <label for="cat" class="legend">Kategorie:</label>
          <select class="select" id="cat" name="cat">
            <option value="all" <?= ($cat===""||strtolower($cat)==="all")?'selected':'' ?>>Alle</option>
            <?php
              $cats = [];
              try {
                  $cats = $pdo->query("SELECT DISTINCT Kategorie FROM participant WHERE COALESCE(Kategorie,'') <> '' ORDER BY Kategorie")->fetchAll(PDO::FETCH_COLUMN);
              } catch (Exception $e) {}
              foreach ($cats as $c) {
                  $sel = ($cat===$c) ? 'selected' : '';
                  echo '<option value="'.htmlspecialchars($c).'" '.$sel.'>'.htmlspecialchars($c).'</option>';
              }
            ?>
          </select>
          <label for="rows" class="legend">Zeilen:</label>
          <input class="select" type="number" id="rows" name="rows" min="0" step="1" value="<?= htmlspecialchars($maxRows) ?>" />
          <label for="refresh" class="legend">Refresh (s):</label>
          <input class="select" type="number" id="refresh" name="refresh" min="0" step="1" value="<?= htmlspecialchars($refresh) ?>" />
          <button class="btn" type="button" id="applyBtn" title="Anwenden">Anwenden</button>
          <button class="btn" type="button" id="fsBtn" title="Taste F: Vollbild">Vollbild</button>
        </form>
      </div>
    </div>

    <div class="sub">
      <span class="legend">Δ Leader = Differenz zum Führenden (Mittelwert). Letzte 3 Zeiten: Δ zur besten Einzelzeit insgesamt.</span><br/>
      <span id="updated">Aktualisiert: –</span>
      <span id="pollInfo" class="legend"></span>
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
        <tbody id="tbody">
          <!-- Filled by JS via AJAX -->
        </tbody>
      </table>
    </div>
  </div>

  <script>
    // -------- Fullscreen (button + "F" key) ----------
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

    // -------- Controls apply -------------
    const applyBtn = document.getElementById('applyBtn');
    applyBtn?.addEventListener('click', () => {
      const params = new URLSearchParams(window.location.search);
      params.set('cat', document.getElementById('cat').value);
      params.set('rows', document.getElementById('rows').value || '0');
      params.set('refresh', document.getElementById('refresh').value || '0');
      // Push to URL without reloading; polling uses these values
      const newUrl = window.location.pathname + '?' + params.toString();
      window.history.replaceState({}, '', newUrl);
      // Re-run poll immediately with new params
      startPolling();
    });

    // -------- Polling without leaving fullscreen -----
    const tbody   = document.getElementById('tbody');
    const updated = document.getElementById('updated');
    const pollInfo= document.getElementById('pollInfo');
    const catLabel= document.getElementById('catLabel');

    let timer = null;

    function getParam(name, fallback) {
      const url = new URL(window.location.href);
      return url.searchParams.get(name) ?? fallback;
    }

    function buildRowHTML(rank, row, leaderMean) {
      const mean = row.mean_str;
      const dlead = row.delta_leader_str;
      const dleadCls = (row.delta_leader_ms > 0) ? 'delta-worse' : (row.delta_leader_ms < 0 ? 'delta-better' : 'delta-better');
      const last3 = (row.last3 || []).map(ch => {
        const cls = ch.delta_cls || '';
        return `<span class="chip ${cls}">${ch.time_str} (${ch.delta_str})</span>`;
      }).join('');
      const nameFull = `${row.Name} ${row.Vorname}`;
      return `
        <tr>
          <td class="rank">${rank}</td>
          <td class="sn">${row.Startnummer}</td>
          <td class="name">${escapeHtml(nameFull)}</td>
          <td class="runs">${row.runs}</td>
          <td class="mean"><strong>${mean}</strong></td>
          <td class="dlead"><span class="${dleadCls}">${dlead}</span></td>
          <td><div class="chips">${last3 || '<span class="legend">–</span>'}</div></td>
        </tr>`;
    }

    function escapeHtml(s) {
      const div = document.createElement('div'); div.innerText = s; return div.innerHTML;
    }

    async function fetchData() {
      const url = new URL(window.location.href);
      url.searchParams.set('ajax', '1'); // ask for JSON only
      // cache-bust to avoid intermediate proxies
      url.searchParams.set('_', Date.now().toString());
      const res = await fetch(url.toString(), { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    }

    async function refreshOnce() {
      try {
        const data = await fetchData();
        updated.textContent = 'Aktualisiert: ' + (data.updated_at || '–');
        catLabel.textContent = data.category || 'Alle Kategorien';

        const rows = data.rows || [];
        let html = '';
        let rank = 0;
        for (const r of rows) {
          if (r.mean_ms === null) continue; // skip riders with no completed runs
          rank++;
          html += buildRowHTML(rank, r, data.leaderMean);
        }

        // riders without runs (optional)
        const noRuns = rows.filter(r => r.mean_ms === null);
        if (noRuns.length > 0) {
          html += `<tr><td colspan="7" class="legend" style="padding-top:1vw;">Teilnehmer ohne gewerteten Lauf:</td></tr>`;
          for (const r of noRuns) {
            const nameFull = `${r.Name} ${r.Vorname}`;
            html += `
              <tr>
                <td class="rank">–</td>
                <td class="sn">${r.Startnummer}</td>
                <td class="name">${escapeHtml(nameFull)}</td>
                <td class="runs">0</td>
                <td class="mean">–</td>
                <td class="dlead">–</td>
                <td>–</td>
              </tr>`;
          }
        }

        tbody.innerHTML = html;
      } catch (e) {
        // show a tiny error hint but keep the layout
        pollInfo.textContent = ' (Polling-Fehler, versuche erneut…)';
        setTimeout(() => { pollInfo.textContent = ''; }, 4000);
      }
    }

    function startPolling() {
      // clear previous
      if (timer) clearInterval(timer);

      // read (possibly changed) refresh param
      const r = parseInt(getParam('refresh', '5'), 10) || 0;
      pollInfo.textContent = r > 0 ? ` (Auto-Refresh: ${r}s)` : ' (kein Auto-Refresh)';
      refreshOnce(); // immediate
      if (r > 0) {
        timer = setInterval(() => {
          // pause when tab is hidden to reduce load
          if (!document.hidden) refreshOnce();
        }, r * 1000);
      }
    }

    // start
    startPolling();
  </script>
</body>
</html>
