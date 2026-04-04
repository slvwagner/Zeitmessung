// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "[Zeitmessung] Project Version: $PROJECT_VERSION\n";
<?php
/****************************************************
 * Race Classement — PROJECTOR MODE (Fullscreen) + Kategorie Filter
 * NOW WITH RANKING MODE:
 *   - mode=avg                         (mean of all completed runs)
 *   - mode=best&n=2&m=3               (best N of last M runs; requires at least N runs)
 *
 * No meta refresh -> AJAX polling keeps fullscreen.
 * Query params:
 *   ?refresh=5&rows=20&cat=Pimped&mode=avg
 *   ?refresh=5&rows=20&cat=Pimped&mode=best&n=2&m=3
 ****************************************************/

// ---------- SETTINGS (GET) ----------
$refresh = max(0, intval($_GET['refresh'] ?? 5));      // seconds; 0 disables auto-poll
$maxRows = max(0, intval($_GET['rows'] ?? 0));         // 0 = no limit
$cat     = trim($_GET['cat'] ?? "");                   // Kategorie filter ("" or "all" = none)
$mode    = strtolower(trim($_GET['mode'] ?? 'avg'));   // 'avg' | 'best'
$nParam  = max(1, intval($_GET['n'] ?? 2));            // for mode=best
$mParam  = max($nParam, intval($_GET['m'] ?? 3));      // for mode=best: M >= N
$isAjax  = isset($_GET['ajax']) && $_GET['ajax'] == '1';

// ---------- DB ----------
$DB_HOST = "localhost";
$DB_NAME = "zeitmessung";
$DB_USER = "root";
$DB_PASS = "";
$DB_CHARSET = "utf8mb4";

// ---------- UTIL ----------
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

// ---------- CONNECT ----------
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

// ---------- QUERY DATA ----------
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
/**FINAL_WHERE**/
ORDER BY Startnummer, finish_time DESC
SQL;

$params = [];
$where = "";
$finalWhere = "";

if ($cat !== "" && strtolower($cat) !== "all") {
    // Apply filter at the final stage where Kategorie is already available
    $finalWhere = "WHERE Kategorie = :cat";
    $params[':cat'] = trim($cat);
    $sql = str_replace("/**WHERE_CLAUSE**/", "", $sql);
    $sql = str_replace("/**FINAL_WHERE**/", $finalWhere, $sql);
} else {
    $sql = str_replace("/**WHERE_CLAUSE**/", "", $sql);
    $sql = str_replace("/**FINAL_WHERE**/", "", $sql);
}

// For debugging only
if (!$isAjax) {
    error_log("Category: " . $cat);
    error_log("SQL: " . $sql);
}

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// ---------- BUILD MODEL ----------
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
            'durations'   => [],  // most recent first (query sorted by finish_time DESC per rider)
        ];
    }
    $byRider[$sn]['durations'][] = $dur;

    if ($globalBestSingle === null || $dur < $globalBestSingle) {
        $globalBestSingle = $dur;
    }
}

// Compute score per rider based on mode
foreach ($byRider as &$r) {
    $durs = $r['durations'];
    $runs = count($durs);

    $r['runs']     = $runs;
    $r['best_ms']  = $runs ? min($durs) : null;
    $r['last3']    = array_slice($durs, 0, 3);

    if ($mode === 'best') {
        // Best N of last M:
        // take last M (most recent first), then pick N smallest among those M
        $lastM = array_slice($durs, 0, $mParam);
        if (count($lastM) >= $nParam) {
            $sorted = $lastM;
            sort($sorted, SORT_NUMERIC);
            $chosen = array_slice($sorted, 0, $nParam);
            $r['score_ms'] = intdiv(array_sum($chosen), $nParam); // average of chosen N
            $r['score_detail'] = [
                'mode' => 'best',
                'n'    => $nParam,
                'm'    => $mParam,
                'picked' => $chosen,
            ];
        } else {
            // Not enough runs -> not ranked
            $r['score_ms'] = null;
            $r['score_detail'] = ['mode'=>'best','n'=>$nParam,'m'=>$mParam,'picked'=>[]];
        }
    } else {
        // Average of all completed runs
        if ($runs > 0) {
            $r['score_ms'] = intdiv(array_sum($durs), $runs);
            $r['score_detail'] = ['mode'=>'avg'];
        } else {
            $r['score_ms'] = null;
            $r['score_detail'] = ['mode'=>'avg'];
        }
    }
}
unset($r);

// Leader score
$leaderScore = null;
foreach ($byRider as $r) {
    if ($r['score_ms'] !== null) {
        $leaderScore = ($leaderScore === null) ? $r['score_ms'] : min($leaderScore, $r['score_ms']);
    }
}

// Classement: sort by score asc, then best single asc, then Startnummer
$classement = array_values($byRider);
usort($classement, function($a, $b) {
    if ($a['score_ms'] === null && $b['score_ms'] === null) return $a['Startnummer'] <=> $b['Startnummer'];
    if ($a['score_ms'] === null) return 1;
    if ($b['score_ms'] === null) return -1;
    if ($a['score_ms'] !== $b['score_ms']) return $a['score_ms'] <=> $b['score_ms'];
    if ($a['best_ms'] !== $b['best_ms'])   return $a['best_ms']   <=> $b['best_ms'];
    return $a['Startnummer'] <=> $b['Startnummer'];
});
if ($maxRows > 0) {
    $classement = array_slice($classement, 0, $maxRows);
}

// ---------- AJAX ----------
if ($isAjax) {
    header('Content-Type: application/json; charset=utf-8');
    $payload = [
        'updated_at' => (new DateTime("now", new DateTimeZone("Europe/Zurich")))->format("Y-m-d H:i:s"),
        'category'   => ($cat !== "" && strtolower($cat) !== "all") ? $cat : "Alle Kategorien",
        'mode'       => $mode,
        'n'          => $nParam,
        'm'          => $mParam,
        'leaderScore'=> $leaderScore,
        'globalBestSingle' => $globalBestSingle,
        'rows' => array_map(function($r) use ($leaderScore, $globalBestSingle) {
            $deltaLeader = ($leaderScore !== null && $r['score_ms'] !== null) ? ($r['score_ms'] - $leaderScore) : null;
            return [
                'Startnummer' => $r['Startnummer'],
                'Name'        => $r['Name'],
                'Vorname'     => $r['Vorname'],
                'runs'        => $r['runs'],
                'best_ms'     => $r['best_ms'],
                'score_ms'    => $r['score_ms'],
                'score_str'   => $r['score_ms'] !== null ? fmt_ms($r['score_ms']) : '–',
                'delta_leader_ms'  => $deltaLeader,
                'delta_leader_str' => ($deltaLeader === null) ? '–' : (($deltaLeader === 0) ? '±0' : fmt_delta($deltaLeader)),
                'last3'       => array_map(function($d) use ($globalBestSingle) {
                    $delta = ($globalBestSingle !== null) ? ($d - $globalBestSingle) : null;
                    return [
                        'time_ms'  => $d,
                        'time_str' => fmt_ms($d),
                        'delta_ms' => $delta,
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

// ---------- NON-AJAX PAGE ----------
$activeCatLabel = ($cat !== "" && strtolower($cat) !== "all") ? $cat : "Alle Kategorien";
$modeLabel = ($mode === 'best') ? ("Beste $nParam aus letzten $mParam") : "Mittelwert aller Läufe";
?>
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Klassement – Projektor (<?= htmlspecialchars($activeCatLabel) ?> – <?= htmlspecialchars($modeLabel) ?>)</title>
  <style>
    :root {
      --bg: #0b1020; --card: #0f172a; --muted: #93a4be; --fg: #e6ebf4;
      --accent: #22d3ee; --good: #22c55e; --warn: #f59e0b; --border: #1f2a40; --row-alt: #111a30;
    }
    html, body { height: 100%; }
    body { margin: 0; background: var(--bg); color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial;
    }
    .wrap { max-width: 1600px; margin: 0 auto; padding: 1.2vw 1.6vw 2vw; }
    .hdr { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
    h1 { font-size: clamp(24px, 3.2vw, 54px); margin: 0; letter-spacing: 0.4px; }
    .sub { color: var(--muted); font-size: clamp(12px, 1.2vw, 20px); margin: 0.6vw 0 1vw; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 1.2vw; padding: 0.6vw 0.6vw 1vw; box-shadow: 0 10px 20px rgba(0,0,0,0.25); }
    .controls { display: flex; gap: 0.6vw; align-items: center; flex-wrap: wrap; }
    .select, .btn {
      border: 1px solid var(--border); background: transparent; color: var(--fg);
      border-radius: 999px; padding: 0.35em 0.8em; font-size: clamp(12px, 1.1vw, 18px);
    }
    /* dark selects + inputs (readable) */
    select.select, input.select { background-color: var(--row-alt); color: var(--fg); }
    select.select option { background-color: var(--card); color: var(--fg); }
    select.select:focus, input.select:focus { outline: 2px solid var(--accent); border-color: var(--accent); }
    select.select:hover, input.select:hover { border-color: var(--muted); }
    select.select option:checked { background-color: var(--row-alt); }
    @supports (-webkit-touch-callout: none) { select.select { -webkit-text-fill-color: var(--fg); } }

    .table { width: 100%; border-collapse: collapse; }
    .table thead th {
      position: sticky; top: 0; background: var(--card); border-bottom: 1px solid var(--border);
      font-weight: 700; text-align: left; padding: 0.7vw 0.6vw; font-size: clamp(14px, 1.4vw, 26px); letter-spacing: 0.3px;
    }
    .table tbody td {
      border-bottom: 1px solid var(--border);
      padding: 0.7vw 0.6vw; font-size: clamp(16px, 1.8vw, 34px);
      vertical-align: middle; font-variant-numeric: tabular-nums;
    }
    .table tbody tr:nth-child(even) { background: var(--row-alt); }
    .rank { width: 5ch; text-align: right; padding-right: 1.2vw !important; }
    .sn { width: 7ch; }
    .name { min-width: 20ch; }
    .runs { width: 6ch; text-align: right; }
    .score, .dlead { white-space: nowrap; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.4vw; }
    .chip { display: inline-block; padding: 0.2vw 0.6vw; border-radius: 999px; border: 1px solid var(--border); font-size: clamp(12px, 1.3vw, 24px); }
    .delta-better { color: var(--good); }
    .delta-worse { color: var(--warn); }
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
      <h1>Klassement – Projektor (<span id="catLabel"><?= htmlspecialchars($activeCatLabel) ?></span> – <span id="modeLabel"><?= htmlspecialchars($modeLabel) ?></span>)</h1>
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

          <label for="mode" class="legend">Modus:</label>
          <select class="select" id="mode" name="mode">
            <option value="avg"  <?= ($mode==='avg')?'selected':'' ?>>Mittel (alle)</option>
            <option value="best" <?= ($mode==='best')?'selected':'' ?>>Beste N aus M</option>
          </select>

          <span id="bestParams" style="display: <?= ($mode==='best')?'inline-flex':'none' ?>; gap:0.4vw; align-items:center;">
            <label for="n" class="legend">N:</label>
            <input class="select" type="number" id="n" name="n" min="1" step="1" value="<?= htmlspecialchars($nParam) ?>" />
            <label for="m" class="legend">M:</label>
            <input class="select" type="number" id="m" name="m" min="<?= htmlspecialchars($nParam) ?>" step="1" value="<?= htmlspecialchars($mParam) ?>" />
          </span>

          <label for="rows" class="legend">Zeilen:</label>
          <input class="select" type="number" id="rows" name="rows" min="0" step="1" value="<?= htmlspecialchars($maxRows) ?>" />
          <label for="refresh" class="legend">Refresh (s):</label>
          <input class="select" type="number" id="refresh" name="refresh" min="0" step="1" value="<?= htmlspecialchars($refresh) ?>" />
          <button class="select" type="button" id="applyBtn" title="Anwenden">Anwenden</button>
          <button class="select" type="button" id="fsBtn" title="Taste F: Vollbild">Vollbild</button>
        </form>
      </div>
    </div>

    <div class="sub">
      <span class="legend">Δ Leader = Differenz zum Führenden (Score). Letzte 3 Zeiten: Δ zur besten Einzelzeit insgesamt.</span><br/>
      <span id="updated">Aktualisiert: –</span>
      <span id="pollInfo" class="legend"></span>
    </div>

    <div class="card">
      <table class="table">
        <thead>
          <tr>
            <th class="rank">Rang</th>
            <th class="sn">Startnr.</th>
            <th class="name">Teilnehmer</th>
            <th class="runs">Läufe</th>
            <th class="score">Zeit</th>
            <th class="dlead">Δ Leader</th>
            <th>Letzte 3 (Δ → Bestzeit)</th>
          </tr>
        </thead>
        <tbody id="tbody"><!-- filled by JS --></tbody>
      </table>
    </div>
  </div>

  <script>
    // Fullscreen
    const fsBtn = document.getElementById('fsBtn');
    function toggleFullscreen() {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(()=>{});
      else document.exitFullscreen().catch(()=>{});
    }
    fsBtn?.addEventListener('click', toggleFullscreen);
    document.addEventListener('keydown', e => { if (e.key.toLowerCase()==='f') toggleFullscreen(); });

    // Controls behavior
    const modeSel = document.getElementById('mode');
    const bestParams = document.getElementById('bestParams');
    const nInput = document.getElementById('n');
    const mInput = document.getElementById('m');
    modeSel?.addEventListener('change', () => {
      bestParams.style.display = (modeSel.value === 'best') ? 'inline-flex' : 'none';
    });
    nInput?.addEventListener('change', () => {
      const n = parseInt(nInput.value||'1',10);
      const m = parseInt(mInput.value||'1',10);
      if (m < n) mInput.value = n;
    });

    const applyBtn = document.getElementById('applyBtn');
    applyBtn?.addEventListener('click', () => {
      const params = new URLSearchParams(window.location.search);
      params.set('cat', document.getElementById('cat').value);
      params.set('rows', document.getElementById('rows').value || '0');
      params.set('refresh', document.getElementById('refresh').value || '0');
      params.set('mode', document.getElementById('mode').value);
      if (document.getElementById('mode').value === 'best') {
        params.set('n', document.getElementById('n').value || '2');
        params.set('m', document.getElementById('m').value || '3');
      } else {
        params.delete('n'); params.delete('m');
      }
      const newUrl = window.location.pathname + '?' + params.toString();
      window.history.replaceState({}, '', newUrl);
      startPolling(); // re-poll immediately with new settings
    });

    // Polling / AJAX
    const tbody   = document.getElementById('tbody');
    const updated = document.getElementById('updated');
    const pollInfo= document.getElementById('pollInfo');
    const catLabel= document.getElementById('catLabel');
    const modeLabel= document.getElementById('modeLabel');

    let timer = null;

    function getParam(name, fallback) {
      const url = new URL(window.location.href);
      return url.searchParams.get(name) ?? fallback;
    }
    function escapeHtml(s){ const d=document.createElement('div'); d.innerText=s; return d.innerHTML; }

    function buildRowHTML(rank, row) {
      const dleadCls = (row.delta_leader_ms > 0) ? 'delta-worse' : (row.delta_leader_ms < 0 ? 'delta-better' : 'delta-better');
      const chips = (row.last3 || []).map(ch => {
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
          <td class="score"><strong>${row.score_str}</strong></td>
          <td class="dlead"><span class="${dleadCls}">${row.delta_leader_str}</span></td>
          <td><div class="chips">${chips || '<span class="legend">–</span>'}</div></td>
        </tr>`;
    }

    async function fetchData() {
      const url = new URL(window.location.href);
      url.searchParams.set('ajax', '1');
      url.searchParams.set('_', Date.now().toString()); // cache-bust
      const res = await fetch(url.toString(), { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    }

    async function refreshOnce() {
      try {
        const data = await fetchData();
        updated.textContent = 'Aktualisiert: ' + (data.updated_at || '–');
        catLabel.textContent = data.category || 'Alle Kategorien';
        modeLabel.textContent = (data.mode === 'best')
          ? `Beste ${data.n} aus letzten ${data.m}`
          : 'Mittelwert aller Läufe';

        const rows = data.rows || [];
        let html = '';
        let rank = 0;
        for (const r of rows) {
          if (r.score_ms === null) continue; // skip riders who don't qualify in this mode
          rank++;
          html += buildRowHTML(rank, r);
        }

        const noRank = rows.filter(r => r.score_ms === null);
        if (noRank.length > 0) {
          html += `<tr><td colspan="7" class="legend" style="padding-top:1vw;">Teilnehmer ohne gültigen Score:</td></tr>`;
          for (const r of noRank) {
            const nameFull = `${r.Name} ${r.Vorname}`;
            html += `
              <tr>
                <td class="rank">–</td>
                <td class="sn">${r.Startnummer}</td>
                <td class="name">${escapeHtml(nameFull)}</td>
                <td class="runs">${r.runs}</td>
                <td class="score">–</td>
                <td class="dlead">–</td>
                <td>–</td>
              </tr>`;
          }
        }

        tbody.innerHTML = html;
      } catch (e) {
        pollInfo.textContent = ' (Polling-Fehler, versuche erneut…)';
        setTimeout(() => { pollInfo.textContent = ''; }, 4000);
      }
    }

    function startPolling() {
      if (timer) clearInterval(timer);
      const r = parseInt(getParam('refresh', '5'), 10) || 0;
      pollInfo.textContent = r > 0 ? ` (Auto-Refresh: ${r}s)` : ' (kein Auto-Refresh)';
      refreshOnce();
      if (r > 0) {
        timer = setInterval(() => { if (!document.hidden) refreshOnce(); }, r * 1000);
      }
    }
    startPolling();
  </script>
</body>
</html>
