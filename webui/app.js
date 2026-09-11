





const $ = s => document.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  kids.flat().forEach(c => n.append(c?.nodeType ? c : document.createTextNode(c)));
  return n;
};
const SVGNS = 'http://www.w3.org/2000/svg';
const sv = (t, a = {}) => {
  const n = document.createElementNS(SVGNS, t);
  for (const [k, v] of Object.entries(a)) n.setAttribute(k, v);
  return n;
};
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : (+v).toFixed(d);

let STATE = null, DERIVED = null, SCENE = null, JOB = null, TERRAIN = null;
let TMESH = null;                       
let V3 = { terr: null, obj: null, tgen: null };
let EXCLUDED = new Set();               
let LIB = { sel: null, cat: '', onlyOn: false };
let TGMESH = null;       



const SENSOR_FIELDS = [
  ['frequency_khz', '중심 주파수', 'kHz', 1, 'EdgeTech 2205 저주파 채널 520 kHz (논문 Table 1)'],
  ['bandwidth_khz', 'CHIRP 대역폭', 'kHz', 1, '488.5–551.5 kHz = 63 kHz (논문 Table 1)'],
  ['horizontal_beam_deg', '수평 빔폭', '°', 0.01, '0.26° (논문 Table 1)'],
  ['vertical_beam_deg', '수직 빔폭', '°', 1, 'EdgeTech 4205 매뉴얼: 50° (±25°)'],
  ['depression_deg', '복각(틸트)', '°', 0.5, 'EdgeTech 4205 매뉴얼: 25° 기계 고정. 바꾸면 실제 장비에 없는 기하가 된다'],
  ['altitude_m', '고도', 'm', 0.1, '해저 윗면(융기부 마루) 기준. 10% 룰: 레인지의 10~20%'],
  ['range_min_m', '최소 거리', 'm', 0.1, '0.5 고정. 0 이면 slant→ground 변환에서 경계값 문제'],
  ['range_max_m', '최대 거리', 'm', 1, 'EdgeTech 2205 540 kHz 정격 최대 150 m'],
  ['range_res_m', '거리 해상도', 'm', 0.01, '논문 Eq.2: c/(2·BW) = 1.19 cm. "auto" 로 두면 그 값'],
  ['add_sigma', '가산 노이즈 σ', '', 1e-6, '센서단 앰비언트/수신기 노이즈 (엔진 AddSigma)'],
  ['mult_sigma', '곱셈 노이즈 σ', '', 0.05, 'Rayleigh 스페클 형상용. contrast 는 Rayleigh 가 고정(0.5227)'],
  ['speckle_strength', '스페클 강도', '', 0.05, '1 = 완전발달 스페클(이론 contrast 0.5227)'],
];
const SENSOR_TOGGLES = [
  ['beam_pattern', '빔 지향성', '레이별 sinc 지향성 |D|⁴ (2-way)'],
  ['tvg', 'TVG', '거리보정. 논문 §3.6 (MacLennan 1986). 정격 150 m 에서 멈춘다'],
  ['absorption', '흡수', 'Francois-Garrison'],
  ['slant_range_correction', 'slant→ground 보정', '논문은 하지 않음 (기본 끔)'],
  ['multipath', '멀티패스(2차 반사)', '논문 Algorithm 1. 구현은 맞으나 레이별 정반사 델타라 '
    + '빈당 1 레이만 기여해 유령이 성긴 점이 된다. 베이스라인 IoU 0.669→0.550. 기본 끔'],
  ['flat_seabed', '평탄 해저', '표시 전용 — 선택한 지형에 heightfield 가 있으면 자동으로 꺼진다. '
    + '취득에는 지형 설정이 쓰이므로 여기서 바꿔도 반영되지 않는다(겹쳐 스폰되면 지형이 통째로 덮인다)'],
];

const TRACK_FIELDS = [
  ['survey_heading_deg', '주행 방위', '°', 1, '격자 전체를 회전. 0=+X, 90=+Y'],
  ['leg_spacing_m', 'leg 간격', 'm', 1, '잔디깎이 leg 사이 거리. 스와스 폭보다 좁으면 겹쳐 찍는다'],
  ['track_x0_m', '주행 시작', 'm', 1, 'leg 시작점 (진행축 좌표)'],
  ['track_x1_m', '주행 끝', 'm', 1, 'leg 끝점. 길수록 ping 수와 취득 시간이 는다'],
];
const CROP_MODES = [
  ['geometric', '기하 판정 (기본·안전)'],
  ['data', '데이터 판정 (바짝 자름)'],
  ['off', '자르지 않음'],
];
const ENV_FIELDS = [
  ['water_density', '해수 밀도 ρ', 'kg/m³', 1, '임피던스 Zw = ρ·c 계산에 쓰인다'],
  ['sound_speed_ms', '음속 c', 'm/s', 1, 'Zw = ρ·c. 거리 bin(c/2BW) 에도 영향'],
  ['wind_speed_ms', '풍속 (해상 상태)', 'm/s', 0.1, 'ASV 동요 → 트랜스듀서 자세 변동 → 워터폴 왜곡. asv 플랫폼에서만 작용'],
  ['current_x_ms', '해류 X', 'm/s', 0.05, 'HoloOcean set_ocean_currents. 플랫폼 표류에만 작용하고 음향에는 영향 없음'],
  ['current_y_ms', '해류 Y', 'm/s', 0.05, '동일'],
];


function numRow(id, label, unit, step, title, value) {
  const inp = el('input', { type: 'number', id: 'f-' + id, step, value: value ?? '' });
  inp.addEventListener('input', scheduleDerive);
  return el('div', { class: 'row', title },
    el('label', {}, label, unit ? el('span', { class: 'u' }, unit) : ''), inp);
}
function chkRow(id, label, title, value) {
  const inp = el('input', { type: 'checkbox', class: 'chk', id: 'f-' + id });
  inp.checked = !!value;
  inp.addEventListener('change', scheduleDerive);
  return el('div', { class: 'row', title }, el('label', {}, label), inp);
}

function buildForms() {
  const s = STATE.sensor;
  const sf = $('#sensor-form'); sf.textContent = '';
  SENSOR_FIELDS.forEach(([k, l, u, st, t]) => {
    let v = s[k];
    if (k === 'range_res_m' && String(v).toLowerCase() === 'auto') v = '';
    sf.append(numRow(k, l, u, st, t, v));
  });
  SENSOR_TOGGLES.forEach(([k, l, t]) => sf.append(chkRow(k, l, t, s[k])));
  
  const cm = el('select', { id: 'f-crop_mode' });
  CROP_MODES.forEach(([v, lbl]) => cm.append(el('option', { value: v }, lbl)));
  cm.value = s.crop_mode || 'geometric';
  cm.addEventListener('change', scheduleDerive);
  sf.append(el('div', { class: 'row', title: '빔이 안 닿는 구간을 어디까지 잘라낼지' },
    el('label', {}, '유효거리 크롭'), cm));

  
  const tf = $('#track-form');
  if (tf) {
    tf.textContent = '';
    TRACK_FIELDS.forEach(([k, l, u, st, t]) => {
      const v = (SCENE && SCENE.survey && SCENE.survey[k] !== undefined) ? SCENE.survey[k] : s[k];
      tf.append(numRow(k, l, u, st, t, v));
    });
  }

  const ef = $('#env-form'); ef.textContent = '';
  ENV_FIELDS.forEach(([k, l, u, st, t]) => {
    let v = s[k];
    if (k.startsWith('current_')) {
      const c = s.current_ms || [0, 0, 0];
      v = k === 'current_x_ms' ? c[0] : c[1];
    }
    ef.append(numRow(k, l, u, st, t, v ?? 0));
  });
  ef.append(el('div', { class: 'kv', id: 'env-kv', style: 'margin-top:8px' }));

  
  const ts = $('#terrain-sel'); ts.textContent = '';
  STATE.terrains.forEach(t => ts.append(el('option', { value: t.id }, t.id)));
  ts.addEventListener('change', () => { loadTerrain(); scheduleDerive(); });

  
  const cf = $('#cat-form'); cf.textContent = '';
  cf.append(el('div', { class: 'objrow objhead' },
    el('div', {}, '카테고리 (재질 · 자산 수 · 실물 크기대)'),
    el('div', {}, '최소'), el('div', {}, '최대')));
  cf.append(el('p', { class: 'hint', style: 'margin:4px 0 8px' },
    '씬을 생성할 때 카테고리마다 이 범위 안에서 개수를 무작위로 뽑고, ',
    '지형 위 임의 위치에 배치한다. 0~0 으로 두면 그 카테고리는 나오지 않는다.'));
  const cats = STATE.scene.object_categories || {};
  Object.entries(cats).forEach(([name, c]) => {
    const [lo, hi] = c.count_range || [0, 0];
    const a = el('input', { type: 'number', id: 'cat-' + name + '-lo', value: lo, step: 1, style: 'width:48px' });
    const b = el('input', { type: 'number', id: 'cat-' + name + '-hi', value: hi, step: 1, style: 'width:48px' });
    cf.append(el('div', {
      class: 'objrow',
      title: (c.tags || []).length ? 'GT 태그: ' + c.tags.join(',') : 'GT 정답 아님 (배경 클러터)'
    },
      el('div', { class: 'nm' }, name,
        el('div', { class: 'tag' }, `${c.material || '(재질 미정)'} · ${catSummary(name)}`)),
      a, b));
  });
}




function catSummary(name) {
  const all = (STATE.catalog || []).filter(o => o.category === name);
  if (!all.length) return '자산 없음';
  const on = all.filter(o => o.available);
  const sz = on.map(o => o.target_size_m).filter(v => v > 0);
  const range = sz.length
    ? `${Math.min(...sz) < 1 ? Math.min(...sz).toFixed(2) : Math.min(...sz).toFixed(1)}~${Math.max(...sz).toFixed(1)} m`
    : '크기 미상';
  return on.length === all.length
    ? `${on.length}종 · ${range}`
    : `${on.length}/${all.length}종 사용 · ${range}`;
}

function readSensor() {
  const out = {};
  SENSOR_FIELDS.forEach(([k]) => {
    const v = $('#f-' + k).value;
    if (k === 'range_res_m' && v === '') { out[k] = 'auto'; return; }
    if (v !== '') out[k] = parseFloat(v);
  });
  SENSOR_TOGGLES.forEach(([k]) => out[k] = $('#f-' + k).checked);
  ENV_FIELDS.forEach(([k]) => {
    if (k.startsWith('current_')) return;
    const v = $('#f-' + k).value;
    if (v !== '') out[k] = parseFloat(v);
  });
  out.current_ms = [parseFloat($('#f-current_x_ms').value) || 0,
                    parseFloat($('#f-current_y_ms').value) || 0, 0];
  const t = STATE.terrains.find(t => t.id === $('#terrain-sel').value);
  if (t) { out.seabed_top_m = t.seabed_top_m; out.flat_seabed = $('#f-flat_seabed').checked; }
  
  out.platform = $('#platform')?.value || 'ideal';
  const nl = parseInt($('#n_legs')?.value, 10);
  if (!Number.isNaN(nl)) out.n_legs = nl;
  if (SCENE?.survey) {
    ['survey', 'leg_spacing_m', 'survey_heading_deg', 'track_x0_m', 'track_x1_m']
      .forEach(k => { if (SCENE.survey[k] !== undefined) out[k] = SCENE.survey[k]; });
  }
  return out;
}


let deriveTimer = null;
function scheduleDerive() { clearTimeout(deriveTimer); deriveTimer = setTimeout(doDerive, 180); }

const sN = k => { const v = parseFloat($('#f-' + k)?.value); return Number.isNaN(v) ? undefined : v; };

let deriveSeq = 0;
async function doDerive() {
  
  
  const seq = ++deriveSeq;
  const r = await fetch('/api/derive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sensor: readSensor() })
  });
  const d = await r.json();
  if (seq !== deriveSeq) return;            
  if (d.error) { console.error(d); return; }
  DERIVED = d;
  renderDerived(); drawMap();
  draw3D();
}


function draw3D() {
  
  if (!DERIVED) return;
  const s = readSensor();
  if (V3.terr) {
    V3.terr.update(TMESH, DERIVED, SCENE, {
      seabed_top_m: s.seabed_top_m,
      range_min_m: s.range_min_m, range_max_m: s.range_max_m,
    }).then(() => renderBeamStats());
  }
}

function kv(host, pairs) {
  host.textContent = '';
  pairs.forEach(([k, v, cls]) => {
    host.append(el('div', { class: 'k' }, k), el('div', { class: 'v ' + (cls || '') }, v));
  });
}

function renderDerived() {
  const d = DERIVED, s = readSensor();
  kv($('#env-kv'), [
    ['임피던스 Zw = ρ·c', (d.impedance_water / 1e6).toFixed(3) + ' ×10⁶ rayl'],
    ['흡수 α @ ' + fmt(s.frequency_khz, 0) + ' kHz', fmt(d.alpha_db_m, 4) + ' dB/m'],
  ]);
  $('#hdr-geom').textContent =
    `고도 ${fmt(d.altitude_m, 1)}m · 복각 ${fmt(s.depression_deg, 0)}° · ${fmt(s.range_max_m, 0)}m`;
  $('#hdr-rays').textContent = `레이 ${d.elev_rays} · bin ${d.range_bins}`;

}

async function loadTerrain() {
  const t = STATE.terrains.find(t => t.id === $('#terrain-sel').value);
  if (!t) return;
  kv($('#terrain-kv'), [
    ['해저 윗면 (마루)', fmt(t.seabed_top_m, 2) + ' m'],
    ['해저 최저점 (골)', fmt(t.seabed_bottom_m, 2) + ' m'],
    ['기복', fmt(Math.abs((t.seabed_bottom_m ?? t.seabed_top_m) - t.seabed_top_m), 2) + ' m'],
    ['범위 X', `${fmt(t.extent_m?.[0], 0)} ~ ${fmt(t.extent_m?.[2], 0)} m`],
    ['범위 Y', `${fmt(t.extent_m?.[1], 0)} ~ ${fmt(t.extent_m?.[3], 0)} m`],
    ['heightfield', t.heightfield_csv ? t.heightfield_csv.split('/').pop() : '없음 (평탄)'],
    ['씬 프리셋', t.scene_preset || '—'],
  ]);
  TERRAIN = null; TMESH = null;
  if (t.heightfield_csv) {
    const csv = encodeURIComponent(t.heightfield_csv.split('/').pop());
    const [g, m] = await Promise.all([
      fetch('/api/terrain?csv=' + csv).then(r => r.json()).catch(() => null),
      fetch('/api/terrain3d?csv=' + csv).then(r => r.json()).catch(() => null),
    ]);
    if (g && g.nx) TERRAIN = g;
    if (m && m.verts) TMESH = m;
  }
  drawMap(); draw3D();
}

function drawMap() {
  const g = $('#viz-map'); g.textContent = '';
  const W = 700, H = 460, pad = 34;
  g.append(sv('rect', { x: 0, y: 0, width: W, height: H, fill: '#0b0f15' }));
  const t = STATE?.terrains.find(t => t.id === $('#terrain-sel').value);
  const ext = t?.extent_m || [-150, -120, 150, 120];
  const [X0, Y0, X1, Y1] = ext;
  const sx = (W - pad * 2) / (X1 - X0), sy = (H - pad * 2) / (Y1 - Y0);
  const s = Math.min(sx, sy);
  const cx = (W - (X1 - X0) * s) / 2, cy = (H - (Y1 - Y0) * s) / 2;
  const px = x => cx + (x - X0) * s;
  const py = y => cy + (Y1 - y) * s;          

  
  if (TERRAIN) {
    const cv = document.createElement('canvas');
    cv.width = TERRAIN.nx; cv.height = TERRAIN.ny;
    const ctx = cv.getContext('2d');
    const img = ctx.createImageData(TERRAIN.nx, TERRAIN.ny);
    const zr = (TERRAIN.zmax - TERRAIN.zmin) || 1;
    for (let i = 0; i < TERRAIN.z.length; i++) {
      const v = TERRAIN.z[i];
      const o = i * 4;
      if (v === null) { img.data[o + 3] = 0; continue; }
      const u = (v - TERRAIN.zmin) / zr;         
      
      img.data[o] = 30 + u * 195;
      img.data[o + 1] = 60 + u * 140;
      img.data[o + 2] = 90 + u * 40;
      img.data[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    const im = sv('image', {
      x: px(TERRAIN.x0), y: py(TERRAIN.y1),
      width: (TERRAIN.x1 - TERRAIN.x0) * s, height: (TERRAIN.y1 - TERRAIN.y0) * s,
      preserveAspectRatio: 'none', opacity: .95
    });
    im.setAttributeNS('http://www.w3.org/1999/xlink', 'href', cv.toDataURL());
    im.setAttribute('href', cv.toDataURL());
    g.append(im);
  } else {
    g.append(sv('rect', { x: px(X0), y: py(Y1), width: (X1 - X0) * s, height: (Y1 - Y0) * s, fill: '#2a2418', opacity: .6 }));
  }
  g.append(sv('rect', {
    x: px(X0), y: py(Y1), width: (X1 - X0) * s, height: (Y1 - Y0) * s,
    fill: 'none', stroke: '#2b3444', 'stroke-width': 1.4
  }));

  
  const d = DERIVED;
  if (d?.legs?.length) {
    const halfSwath = d.swath_m;
    d.legs.forEach((L, i) => {
      const dx = L.p1[0] - L.p0[0], dy = L.p1[1] - L.p0[1];
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;        
      const poly = [
        [L.p0[0] + nx * halfSwath, L.p0[1] + ny * halfSwath],
        [L.p1[0] + nx * halfSwath, L.p1[1] + ny * halfSwath],
        [L.p1[0] - nx * halfSwath, L.p1[1] - ny * halfSwath],
        [L.p0[0] - nx * halfSwath, L.p0[1] - ny * halfSwath],
      ].map(p => `${px(p[0])},${py(p[1])}`).join(' ');
      g.append(sv('polygon', { points: poly, fill: '#4aa8ff', opacity: .13, stroke: '#4aa8ff', 'stroke-width': .6, 'stroke-opacity': .4 }));
      g.append(sv('line', {
        x1: px(L.p0[0]), y1: py(L.p0[1]), x2: px(L.p1[0]), y2: py(L.p1[1]),
        stroke: '#f0f6fc', 'stroke-width': 1.8
      }));
      g.append(sv('circle', { cx: px(L.p0[0]), cy: py(L.p0[1]), r: 3.4, fill: '#3fb950' }));
    });
  }

  
  if (SCENE?.objects) {
    SCENE.objects.forEach(o => {
      const isWreck = (o.tags || []).includes('wreck');
      g.append(sv('circle', {
        cx: px(o.position_m[0]), cy: py(o.position_m[1]),
        r: isWreck ? 5 : 2.6,
        fill: isWreck ? '#d29922' : '#8b98a8',
        stroke: '#0b0f15', 'stroke-width': 1
      }));
    });
  }

  const txt = (x, y, t, c = '#5f6c7c', anc = 'start', sz = 10) => {
    const n = sv('text', { x, y, fill: c, 'font-size': sz, 'text-anchor': anc, 'font-family': 'ui-monospace,monospace' });
    n.textContent = t; g.append(n);
  };
  txt(px(X0), py(Y1) - 8, `${t?.id || '평탄'} — ${(X1 - X0).toFixed(0)} × ${(Y1 - Y0).toFixed(0)} m`, '#8b98a8');
  if (TERRAIN) txt(px(X1), py(Y1) - 8, `z ${fmt(TERRAIN.zmin, 1)} ~ ${fmt(TERRAIN.zmax, 1)} m`, '#8b98a8', 'end');
  if (d?.swath_m) txt(px(X0), py(Y0) + 16, `스와스 한쪽 ${fmt(d.swath_m, 0)} m · leg ${d.legs?.length || 0}개`, '#4aa8ff');
  
  if (d?.legs?.length && t?.extent_m) {
    const out = d.legs.some(L => {
      const dx = L.p1[0] - L.p0[0], dy = L.p1[1] - L.p0[1], len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      return [[L.p0, 1], [L.p0, -1], [L.p1, 1], [L.p1, -1]].some(([p, sgn]) => {
        const x = p[0] + nx * d.swath_m * sgn, y = p[1] + ny * d.swath_m * sgn;
        return x < X0 || x > X1 || y < Y0 || y > Y1;
      });
    });
    if (out) txt(px(X1), py(Y0) + 16, '⚠ 스와스가 지형 밖으로 나간다 (검은 영역 발생)', '#d29922', 'end');
  }
}





async function saveSensor() {
  const sensor = readSensor();
  
  ['seabed_top_m', 'platform', 'n_legs', 'survey', 'leg_spacing_m',
   'survey_heading_deg', 'track_x0_m', 'track_x1_m'].forEach(k => delete sensor[k]);
  await fetch('/api/save_sensor', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sensor })
  });
}


async function saveScene() {
  const cats = {};
  Object.keys(STATE.scene.object_categories || {}).forEach(n => {
    const lo = parseInt($('#cat-' + n + '-lo').value, 10);
    const hi = parseInt($('#cat-' + n + '-hi').value, 10);
    if (!Number.isNaN(lo) && !Number.isNaN(hi)) cats[n] = { count_range: [lo, hi] };
  });
  await fetch('/api/save_scene', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scene: { object_categories: cats } })
  });
}

async function saveAll() {
  await saveSensor(); await saveScene();
  flash($('#btn-save'), '저장됨');
}

function flash(btn, msg) {
  const o = btn.textContent; btn.textContent = msg;
  setTimeout(() => btn.textContent = o, 1300);
}

async function genManifest() {
  const b = $('#btn-manifest'); b.disabled = true; b.textContent = '생성 중…';
  await saveScene();          
  const r = await fetch('/api/manifest', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      n: 1, seed: parseInt($('#seed').value, 10) || 0,
      terrain: $('#terrain-sel').value, out: 'webui_scene.jsonl',
      wreck_tilt_deg: parseFloat($('#manual-wreck-tilt').value),
      
      survey_override: $('#use-my-sensor').checked ? {
        range_max_m: sN('range_max_m'), range_min_m: sN('range_min_m'),
        altitude_m: sN('altitude_m'), depression_deg: sN('depression_deg'),
        range_res_m: $('#f-range_res_m').value === '' ? undefined : sN('range_res_m'),
        wind_speed_ms: sN('wind_speed_ms'),
        n_legs: parseInt($('#n_legs').value, 10) || undefined,
        platform: $('#platform').value,
      } : null
    })
  });
  const d = await r.json();
  b.disabled = false; b.textContent = '씬 생성';
  if (!d.scenes?.length) { alert('씬 생성 실패:\n' + (d.stdout || d.error)); return; }
  SCENE = d.scenes[0];
  SCENE._path = d.path;
  annotateScene();
  const s = SCENE.survey;
  
  const counts = {};
  (SCENE.objects || []).forEach(o => {
    const c = String(o.catalog_id || '?').split('/')[0];
    counts[c] = (counts[c] || 0) + 1;
  });
  kv($('#scene-kv'), [
    ['씬 ID', SCENE.scene_id],
    ['지형', SCENE.terrain.id],
    ['오브젝트', Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(', ') || '없음'],
    ['레인지', fmt(s.range_max_m, 1) + ' m'],
    ['고도 (마루/골)', `${fmt(s.altitude_m, 2)} / ${fmt(s.altitude_worst_m, 2)} m`],
    ['복각', fmt(s.depression_deg, 1) + '°'],
    ['leg', `${s.n_legs}개 · 간격 ${fmt(s.leg_spacing_m, 0)} m · 방위 ${fmt(s.survey_heading_deg, 0)}°`],
    ['매니페스트', d.path],
  ]);
  renderPlaced();
  
  
  
  
  
  if (!$('#use-my-sensor').checked) {
    ['range_max_m', 'depression_deg', 'altitude_m', 'range_res_m', 'wind_speed_ms']
      .forEach(k => { if (s[k] !== undefined && $('#f-' + k)) $('#f-' + k).value = s[k]; });
    $('#n_legs').value = s.n_legs;
  }
  if ($('#f-flat_seabed')) $('#f-flat_seabed').checked = !SCENE.terrain.heightfield_csv;
  doDerive();
}

async function preview() {
  
  
  const ov = formOverrides();
  
  
  if (!SCENE) ov.terrain = $('#terrain-sel').value;
  const r = await fetch('/api/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      manifest: SCENE ? SCENE._path : null, manifest_index: 0,
      size: 768, pitch_deg: 90, overrides: ov
    })
  }).then(r => r.json());
  JOB = r.job;
  $('#btn-preview').disabled = true;
  $('#btn-stop').disabled = false;
  $('#log').textContent = '미리보기 촬영 중… (시뮬레이터 기동에 30~60초)\n';
  $('#preview-box').style.display = 'none';
  pollJob();
}












function formOverrides() {
  const s = readSensor();
  const ov = {};
  
  Object.entries(s).forEach(([k, v]) => {
    if (v === undefined || v === '') return;
    if (k === 'survey') return;                      
    ov[k] = Array.isArray(v) ? JSON.stringify(v) : v;
  });
  
  
  
  
  
  
  
  delete ov.seabed_top_m;
  delete ov.flat_seabed;
  ov.range_res_m = $('#f-range_res_m').value || 'auto';
  ov.n_legs = parseInt($('#n_legs').value, 10) || 1;
  const sv = $('#f-survey_heading_deg'); if (sv && sv.value !== '') ov.survey_heading_deg = parseFloat(sv.value);
  const ls = $('#f-leg_spacing_m'); if (ls && ls.value !== '') ov.leg_spacing_m = parseFloat(ls.value);
  const x0 = $('#f-track_x0_m'); if (x0 && x0.value !== '') ov.track_x0_m = parseFloat(x0.value);
  const x1 = $('#f-track_x1_m'); if (x1 && x1.value !== '') ov.track_x1_m = parseFloat(x1.value);
  const cm = $('#f-crop_mode'); if (cm) ov.crop_mode = cm.value;
  return ov;
}

async function pollJob() {
  if (!JOB) return;
  const r = await fetch('/api/job?id=' + JOB);
  const d = await r.json();
  $('#log').textContent = d.log || '';
  $('#log').scrollTop = $('#log').scrollHeight;
  
  
  const pm = [...(d.log || '').matchAll(
    /\[progress\] (\S+) (\d+)\/(\d+) ([\d.]+)% 경과 (\d+)s 남음 (\d+|-)s?/g)].pop();
  const em = [...(d.log || '').matchAll(/\[dataset-estimate\] (\{[^\n]+\})/g)].pop();
  let estimate = null;
  if (em) { try { estimate = JSON.parse(em[1]); } catch (_) {} }
  const durationText = sec => {
    sec = Math.max(0, Math.round(sec || 0));
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return (d ? d + '일 ' : '') + (h ? h + '시간 ' : '') + m + '분';
  };
  const bar = $('#prog');
  if (pm && d.running) {
    bar.style.display = '';
    $('#prog-fill').style.width = pm[4] + '%';
    $('#prog-txt').textContent =
      `${pm[1]} ${pm[2]}/${pm[3]} (${pm[4]}%) · 경과 ${pm[5]}s`
      + (estimate ? ` · 전체 남음 약 ${durationText(estimate.remaining_seconds)}`
        : (pm[6] !== '-' ? ` · 남음 약 ${pm[6]}s` : ''));
  } else if (!d.running) {
    bar.style.display = 'none';
  }
  const stage = pm ? `${pm[1]} ${pm[4]}%`
    : (/스폰|프리셋/.test(d.log || '') ? '씬 스폰 중'
      : (/Elevation Rays/.test(d.log || '') ? '시뮬레이터 기동 중' : '준비 중'));
  const dsState = d.dataset_status;
  const finishText = estimate ? new Date(estimate.finish_epoch * 1000).toLocaleString('ko-KR')
    + ` (약 ${durationText(estimate.remaining_seconds)} 남음)` : '—';
  const endState = dsState === 'interrupted' ? '사용자 중단 — 완료 데이터 보존됨'
    : dsState === 'complete' ? '완료'
    : dsState === 'planned' ? '계획 생성 완료'
    : (d.rc === 0 ? '완료' : `종료 (rc=${d.rc})`);
  kv($('#run-kv'), [
    ['상태', d.running ? '실행 중 — ' + stage : endState,
      d.running ? '' : (d.rc === 0 || dsState === 'interrupted' ? 'ok' : 'warn')],
    ['경과', d.elapsed + ' s'],
    ['예상 종료', finishText],
    ['출력', d.outdir || '—'],
  ]);
  if (d.running) { setTimeout(pollJob, 1200); return; }
  $('#btn-stop').disabled = true;
  $('#btn-preview').disabled = false;
  $('#btn-large').disabled = false; $('#btn-large-dry').disabled = false;
  if (d.preview) {
    const url = '/file?path=' + encodeURIComponent(d.preview) + '&t=' + Date.now();
    $('#preview-img').src = url;
    $('#preview-img').onclick = () => openModal(url);
    $('#preview-box').style.display = '';
    
    const m = (d.log || '').match(/화면 폭 약 (\d+) m/);
    $('#preview-cap').textContent = '시뮬레이터 탑뷰 미리보기'
      + (m ? ` — 화면 폭 약 ${m[1]} m · 오른쪽=+X, 위=+Y` : '');
  }
  showImages(d.images || []);
  loadRuns();
}

async function restoreActiveJob() {
  
  
  try {
    const d = await (await fetch('/api/active_job')).json();
    if (!d || !d.id || !d.running) return;
    JOB = d.id;
    $('#btn-large').disabled = true; $('#btn-large-dry').disabled = true;
    $('#btn-preview').disabled = true;
    $('#btn-stop').disabled = false;
    $('#log').textContent = '실행 중인 취득 작업에 다시 연결 중…\n';
    pollJob();
  } catch (e) {
    console.warn('실행 중 취득 복원 실패:', e);
  }
}

function showImages(imgs) {
  const host = $('#result'); host.textContent = '';
  imgs.filter(i => i.kind !== 'trueaspect').forEach(i => {
    const url = '/file?path=' + encodeURIComponent(i.path);
    const f = el('figure', { onclick: () => openModal(url) },
      el('img', { src: url, loading: 'lazy' }),
      el('figcaption', {}, ({mask: '[GT 마스크] ', waterfall: '[SSS] ',
        bbox_overlay: '[GT bbox] '}[i.kind] || '') + i.name));
    host.append(f);
  });
  if (!imgs.length) host.append(el('div', { style: 'color:#8b98a8;font-size:12px' }, '생성된 이미지가 없습니다.'));
}

async function loadRuns() {
  const r = await fetch('/api/runs');
  const runs = await r.json();
  const host = $('#runs'); host.textContent = '';
  runs.forEach(run => {
    const url = '/file?path=' + encodeURIComponent(run.thumb);
    host.append(el('figure', { onclick: () => openModal(url) },
      el('img', { src: url, loading: 'lazy' }),
      el('figcaption', {}, `${run.id} (${run.n})`)));
  });
}

function openModal(url) { $('#modal-img').src = url; $('#modal').classList.add('on'); }





function annotateScene() {
  if (!SCENE?.objects) return;
  const byId = new Map((STATE.catalog || []).map(o => [o.id, o]));
  SCENE.objects.forEach(o => {
    const c = byId.get(o.catalog_id);
    o._final_size_m = (c && c.measured_max_m) ? c.measured_max_m * o.scale : null;
    o._catalog = c || null;
  });
}




function renderPlaced() {
  const host = $('#placed'); if (!host) return;
  host.textContent = '';
  const objs = SCENE?.objects || [];
  if (!objs.length) {
    host.append(el('div', { class: 'hint' }, '씬을 생성하면 배치된 오브젝트가 여기 나옵니다.'));
    return;
  }
  objs.forEach((o, i) => {
    const wreck = (o.tags || []).includes('wreck');
    const size = o._final_size_m;
    const node = el('div', { class: 'placed-item' + (wreck ? ' wreck' : '') },
      el('div', { class: 'nm' },
        (wreck ? '★ ' : '') + o.catalog_id.split('/').pop(),
        el('div', { class: 'pos' },
          `${o.catalog_id.split('/')[0]} · `
          + (size ? `${size.toFixed(1)} m · ` : '')
          + `(${o.position_m[0].toFixed(0)}, ${o.position_m[1].toFixed(0)}) m · `
          + `매몰 ${(o.burial_ratio*100).toFixed(0)}%`)),
      el('button', { onclick: e => { e.stopPropagation(); removePlaced(i); } }, '삭제'));
    
    node.addEventListener('mouseenter', () => showObjPop(node, o));
    node.addEventListener('mouseleave', hideObjPop);
    
    node.addEventListener('click', () => selectPlaced(i));
    node.addEventListener('dblclick', () => {
      hideObjPop();
      $('#lib').classList.add('on');
      libRender(); V3.obj?.sc.refresh(); libSelect(o.catalog_id);
    });
    host.append(node);
  });
}


function renderBeamStats() {
  const host = $('#beam-kv'); if (!host) return;
  const b = V3.terr?.beamStats;
  if (!b) { host.textContent = ''; return; }
  const tot = b.hit + b.miss;
  kv(host, [
    ['빔 커버 복각', `${b.lo.toFixed(1)}° ~ ${b.hi.toFixed(1)}°`],
    ['취득 창 (경사거리)', `${b.rMin.toFixed(1)} ~ ${b.rMax.toFixed(0)} m`],
    ['지형에 닿은 레이', `${b.hit} / ${tot} (${(100*b.hit/Math.max(tot,1)).toFixed(0)}%)`,
      b.hit === tot ? 'ok' : (b.hit === 0 ? 'bad' : 'warn')],
    ['최대거리를 다 쓰고도 못 닿음', `${b.miss} 개`, b.miss ? 'warn' : 'ok'],
  ]);
}


let SEL = -1;                       

function selectPlaced(i) {
  SEL = (SEL === i) ? -1 : i;
  if (V3.terr) V3.terr.selected = SEL;
  document.querySelectorAll('.placed-item').forEach((n, k) =>
    n.classList.toggle('sel', k === SEL));
  renderSelEdit();
  draw3D();
}

function renderSelEdit() {
  const host = $('#sel-edit'); if (!host) return;
  const o = SCENE?.objects?.[SEL];
  if (!o) { host.style.display = 'none'; return; }
  host.style.display = '';
  host.textContent = '';
  host.append(el('div', { class: 'se-title' }, '선택: ' + o.catalog_id));
  const num = (label, unit, val, step, on) => {
    const inp = el('input', { type: 'number', step, value: val });
    inp.addEventListener('input', () => { on(parseFloat(inp.value)); });
    return el('div', { class: 'row' },
      el('label', {}, label, el('span', { class: 'u' }, unit)), inp);
  };
  host.append(num('X', 'm', o.position_m[0].toFixed(1), 1,
    v => { if (!Number.isNaN(v)) { o.position_m[0] = v; pushSel(); } }));
  host.append(num('Y', 'm', o.position_m[1].toFixed(1), 1,
    v => { if (!Number.isNaN(v)) { o.position_m[1] = v; pushSel(); } }));
  host.append(num('방위(yaw)', '°', o.rotation_deg[2].toFixed(0), 5,
    v => { if (!Number.isNaN(v)) { o.rotation_deg[2] = v; pushSel(); } }));
  host.append(num('기울기(roll)', '°', o.rotation_deg[0].toFixed(0), 1,
    v => { if (!Number.isNaN(v)) { o.rotation_deg[0] = Math.min(Math.max(v, -180), 180); pushSel(); } }));
  host.append(num('기울기(pitch)', '°', o.rotation_deg[1].toFixed(0), 1,
    v => { if (!Number.isNaN(v)) { o.rotation_deg[1] = Math.min(Math.max(v, -180), 180); pushSel(); } }));
  host.append(num('매몰률', '%', (o.burial_ratio * 100).toFixed(0), 5,
    v => { if (!Number.isNaN(v)) { o.burial_ratio = Math.min(Math.max(v/100, 0), 0.95); pushSel(); } }));
  host.append(el('div', { class: 'se-status ok', id: 'sel-status' },
    '바꾸면 자동으로 매니페스트에 저장됩니다.'));
  host.append(el('div', { class: 'btns' },
    el('button', { onclick: () => selectPlaced(SEL) }, '선택 해제')));
  host.append(el('p', { class: 'hint' },
    '매몰률은 오브젝트를 해저 아래로 얼마나 묻을지다. 엔진이 스폰 시 메쉬 바운딩을 '
    + '기준으로 그만큼 내리고, 3D 뷰도 같은 만큼 가라앉혀 보여준다. '
    + '소나에는 노출된 부분만 잡힌다.'));
}




let saveTimer = null;
function pushSel() {
  annotateScene(); renderPlaced();
  document.querySelectorAll('.placed-item').forEach((n, k) =>
    n.classList.toggle('sel', k === SEL));
  draw3D();
  const st = $('#sel-status');
  if (st) { st.textContent = '저장 중…'; st.className = 'se-status busy'; }
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveScene3D, 500);
}

async function saveScene3D() {
  if (!SCENE?._path) return;
  const r = await fetch('/api/manifest_save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: SCENE._path, index: 0, scene: SCENE })
  }).then(r => r.json()).catch(e => ({ error: String(e) }));
  const st = $('#sel-status');
  if (!st) return;
  if (r.error) { st.textContent = '저장 실패: ' + r.error; st.className = 'se-status bad'; }
  else { st.textContent = `저장됨 — 이대로 '데이터 취득' 을 누르면 됩니다 (씬 재생성 불필요)`;
         st.className = 'se-status ok'; }
}




function bindTerrainEdit() {
  const cv = $('#cv-terrain'); if (!cv || !V3.terr) return;
  const sc = V3.terr.sc;
  let mode = null, start = null;
  const local = e => {
    const r = cv.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  cv.addEventListener('pointerdown', e => {
    const o = SCENE?.objects?.[SEL];
    if (!e.ctrlKey || !o) return;
    e.stopPropagation(); e.preventDefault();
    const [lx, ly] = local(e);
    const hit = Scene3D.hitPlaneZ(sc.rayFromScreen(lx, ly), o.position_m[2]);
    mode = e.shiftKey ? 'rot' : 'move';
    start = { lx, ly, hit, pos: o.position_m.slice(), yaw: o.rotation_deg[2] };
    cv.setPointerCapture(e.pointerId);
  }, true);
  cv.addEventListener('pointermove', e => {
    if (!mode) return;
    const o = SCENE?.objects?.[SEL]; if (!o) { mode = null; return; }
    e.stopPropagation(); e.preventDefault();
    const [lx, ly] = local(e);
    if (mode === 'move') {
      const hit = Scene3D.hitPlaneZ(sc.rayFromScreen(lx, ly), o.position_m[2]);
      if (hit && start.hit) {
        o.position_m[0] = start.pos[0] + (hit[0] - start.hit[0]);
        o.position_m[1] = start.pos[1] + (hit[1] - start.hit[1]);
      }
    } else {
      o.rotation_deg[2] = start.yaw + (lx - start.lx) * 0.8;
    }
    pushSel(); renderSelEdit();
  }, true);
  const end = e => {
    if (!mode) return;
    e.stopPropagation();
    mode = null;
    try { cv.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  cv.addEventListener('pointerup', end, true);
  cv.addEventListener('pointercancel', end, true);
}


const THUMBS = new Map();           
let thumbScene = null, popTimer = null;

function thumbFor(id, sizeM) {
  if (THUMBS.has(id)) return THUMBS.get(id);
  const p = (async () => {
    const m = await fetch('/api/mesh?id=' + encodeURIComponent(id)
      + (sizeM ? '&size=' + sizeM : '')).then(r => r.json()).catch(() => null);
    if (!m || m.error || !m.verts) return null;
    if (!thumbScene) {
      const cv = document.createElement('canvas');
      cv.width = 260; cv.height = 190;
      cv.style.cssText = 'position:fixed;left:-9999px;width:260px;height:190px';
      document.body.append(cv);
      
      thumbScene = new Scene3D(cv, { keepBuffer: true, dist: 4, pitch: 0.4, yaw: -0.9 });
      if (thumbScene.dead) return null;
    }
    const sc = thumbScene;
    sc.clear();
    const wreck = (m.tags || []).includes('wreck');
    sc.addMesh(m.verts, m.faces, wreck ? [0.86,0.70,0.32,1] : [0.78,0.81,0.86,1],
               { flat: true });
    const r = Math.max(...m.bbox_m) / 2;
    sc.userMoved = false;
    sc.frame([0, 0, 0], r * 1.45, true);
    return sc.cv.toDataURL('image/png');
  })();
  THUMBS.set(id, p);
  return p;
}

function objPop() {
  let n = $('#objpop');
  if (!n) {
    n = el('div', { id: 'objpop' });
    document.body.append(n);
  }
  return n;
}

async function showObjPop(anchor, o) {
  clearTimeout(popTimer);
  const n = objPop();
  const c = o._catalog || {};
  const size = o._final_size_m;
  n.textContent = '';
  n.append(el('div', { class: 'op-title' }, o.catalog_id));
  const imgHost = el('div', { class: 'op-img' }, el('span', { class: 'op-wait' }, '메쉬 불러오는 중…'));
  n.append(imgHost);
  n.append(el('div', { class: 'op-kv' },
    el('span', {}, '배치 크기'), el('b', {}, size ? size.toFixed(2) + ' m' : '—'),
    el('span', {}, '카탈로그 실측'), el('b', {}, c.measured_max_m ? c.measured_max_m + ' m' : '—'),
    el('span', {}, '배율'), el('b', {}, o.scale.toFixed(3)),
    el('span', {}, '위치'), el('b', {}, `(${o.position_m[0].toFixed(1)}, ${o.position_m[1].toFixed(1)})`),
    el('span', {}, '회전'), el('b', {}, o.rotation_deg.map(v => v.toFixed(0)).join(', ') + '°'),
    el('span', {}, '매몰'), el('b', {}, (o.burial_ratio * 100).toFixed(0) + '%'),
    el('span', {}, '태그'), el('b', {}, (o.tags || []).join(', ') || '없음')));
  n.append(el('div', { class: 'op-hint' }, '클릭하면 자산 라이브러리에서 자세히'));

  const r = anchor.getBoundingClientRect();
  n.style.left = Math.min(r.right + 10, innerWidth - 300) + 'px';
  n.style.top = Math.min(r.top, innerHeight - 330) + 'px';
  n.classList.add('on');

  const url = await thumbFor(o.catalog_id, size);
  if (!n.classList.contains('on')) return;
  imgHost.textContent = '';
  if (url) imgHost.append(el('img', { src: url, alt: '' }));
  else imgHost.append(el('span', { class: 'op-wait' }, '메쉬를 읽지 못했습니다'));
}

function hideObjPop() {
  popTimer = setTimeout(() => objPop().classList.remove('on'), 120);
}

async function removePlaced(i) {
  SCENE.objects.splice(i, 1);
  await fetch('/api/manifest_save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: SCENE._path, index: 0, scene: SCENE })
  });
  renderPlaced();
  
  const counts = {};
  SCENE.objects.forEach(o => {
    const c = String(o.catalog_id || '?').split('/')[0];
    counts[c] = (counts[c] || 0) + 1;
  });
  const cell = [...document.querySelectorAll('#scene-kv > div')]
    .find((d, k, arr) => arr[k - 1]?.textContent === '오브젝트');
  if (cell) cell.textContent = Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(', ') || '없음';
  draw3D();
}



const TG_GROUPS = [
  ['범위 · 격자', [
    ['x0_m', 'X 시작', 'm', 10, -150, '지형 범위. 스와스가 이 밖으로 나가면 검은 영역이 생긴다'],
    ['x1_m', 'X 끝', 'm', 10, 150, ''],
    ['y0_m', 'Y 시작', 'm', 10, -120, ''],
    ['y1_m', 'Y 끝', 'm', 10, 120, ''],
    ['cell_m', '격자 간격', 'm', 0.5, 1.0, '기존 지형과 같은 1 m. 촘촘할수록 정점 수가 제곱으로 는다'],
  ]],
  ['기본 수심 · 광역 경사', [
    ['base_depth_m', '기준 수심', 'm', 0.5, 18.0, '해저면 z = -수심'],
    ['slope_deg', '경사', '°', 0.05, 0.0, '대륙붕의 광역 경사는 보통 0.02~1°'],
    ['slope_dir_deg', '경사 방위', '°', 5, 0.0, '0=+X 방향으로 깊어짐'],
  ]],
  ['사구 (모래파)', [
    ['sand_wave_amp_m', '파고', 'm', 0.1, 0.0, '대륙붕에 흔한 규칙적 기복. Ashley(1990) dune 분류에서 파고 0.5~10 m'],
    ['sand_wave_len_m', '파장', 'm', 5, 60.0, '같은 분류에서 파장 10~500 m'],
    ['sand_wave_dir_deg', '마루 방위', '°', 5, 0.0, ''],
  ]],
  ['능선 · 골', [
    ['ridge_amp_m', '진폭', 'm', 0.5, 0.0, '2D 사인. 방향성이 뚜렷해 파이프라인 확인이 쉽다'],
    ['ridge_len_x_m', 'X 파장', 'm', 10, 280.0, ''],
    ['ridge_len_y_m', 'Y 파장', 'm', 10, 380.0, ''],
  ]],
  ['분지 · 둔덕', [
    ['bowl_amp_m', '깊이', 'm', 0.5, 0.0, '양수=분지(움푹), 음수=둔덕(솟음)'],
    ['bowl_radius_m', '반경', 'm', 5, 90.0, ''],
    ['bowl_cx_m', '중심 X', 'm', 10, 0.0, ''],
    ['bowl_cy_m', '중심 Y', 'm', 10, 0.0, ''],
  ]],
  ['수로', [
    ['channel_depth_m', '깊이', 'm', 0.5, 0.0, '한 방향으로 뻗은 가우시안 트로프'],
    ['channel_width_m', '폭', 'm', 5, 30.0, ''],
    ['channel_dir_deg', '방위', '°', 5, 0.0, ''],
    ['channel_offset_m', '중심 오프셋', 'm', 10, 0.0, ''],
  ]],
  ['미세 기복', [
    ['roughness_amp_m', '진폭', 'm', 0.05, 0.0, '프랙탈 값잡음. 소나 입사각을 국소적으로 흔들어 스페클 대비를 키운다'],
    ['roughness_cell_m', '셀 크기', 'm', 1, 8.0, ''],
    ['roughness_octaves', '옥타브', '', 1, 4, ''],
    ['seed', '시드', '', 1, 0, '같은 시드 = 같은 지형(재현 가능)'],
  ]],
];

function tgBuildForm() {
  const host = $('#tg-form'); host.textContent = '';
  host.append(el('h4', {}, '이름'));
  host.append(el('div', { class: 'row', title: '파일명이 된다. 영문/숫자/_/- 만' },
    el('label', {}, '지형 id'),
    el('input', { type: 'text', id: 'tg-name', value: 'my_field_v1' })));

  TG_GROUPS.forEach(([title, fields]) => {
    host.append(el('h4', {}, title));
    fields.forEach(([k, l, u, st, dv, tt]) => {
      const inp = el('input', { type: 'number', id: 'tg-' + k, step: st, value: dv });
      inp.addEventListener('input', tgSchedule);
      host.append(el('div', { class: 'row', title: tt },
        el('label', {}, l, u ? el('span', { class: 'u' }, u) : ''), inp));
    });
  });

  host.append(el('h4', {}, '퇴적물 재질'));
  [['baseline_material', '융기부(기본)', 'coarse_silt'],
   ['soft_material', '저지대(연질)', 'very_fine_silt']].forEach(([k, l, dv]) => {
    const s = el('select', { id: 'tg-' + k });
    (STATE.materials || []).forEach(m => s.append(el('option', { value: m.id },
      `${m.name}  (${m.r2_db.toFixed(1)} dB)`)));
    s.value = dv;
    s.addEventListener('change', tgSchedule);
    host.append(el('div', {
      class: 'row',
      title: '괄호는 해수 대비 강도 반사계수 R². 클수록 소나에 밝게 찍힌다'
    }, el('label', {}, l), s));
  });
}

function tgParams() {
  const p = {};
  TG_GROUPS.forEach(([, fields]) => fields.forEach(([k]) => {
    const v = parseFloat($('#tg-' + k).value);
    if (!Number.isNaN(v)) p[k] = v;
  }));
  p.baseline_material = $('#tg-baseline_material').value;
  p.soft_material = $('#tg-soft_material').value;
  return p;
}

let tgTimer = null;
function tgSchedule() { clearTimeout(tgTimer); tgTimer = setTimeout(tgPreview, 250); }

async function tgPreview() {
  $('#tg-stat').textContent = '계산 중…';
  const m = await fetch('/api/terrain_preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params: tgParams() })
  }).then(r => r.json()).catch(e => ({ error: String(e) }));
  if (m.error) { $('#tg-stat').textContent = '오류'; kv($('#tg-kv'), [['오류', m.error, 'bad']]); return; }
  TGMESH = m;
  $('#tg-stat').textContent = `${m.grid[0]}×${m.grid[1]} · 기복 ${m.relief_m.toFixed(2)} m`;
  V3.tgen?.update(m, null, null, {});
  const alt10 = m.relief_m;               
  kv($('#tg-kv'), [
    ['격자', `${m.grid[0]} × ${m.grid[1]} = ${(m.grid[0]*m.grid[1]).toLocaleString()} 정점`],
    ['수심', `${m.depth_min_m.toFixed(2)} ~ ${m.depth_max_m.toFixed(2)} m`],
    ['해저면 z', `${m.seabed_bottom_m.toFixed(2)} ~ ${m.seabed_top_m.toFixed(2)} m`],
    ['기복', m.relief_m.toFixed(2) + ' m'],
    ['10% 룰이 요구하는 최소 고도', alt10.toFixed(2) + ' m',
      alt10 > 25 ? 'warn' : 'ok'],
  ]);
}

async function tgCreate() {
  const name = $('#tg-name').value.trim();
  const b = $('#tg-create'); b.disabled = true; b.textContent = '생성 중…';
  const r = await fetch('/api/terrain_create', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, params: tgParams() })
  }).then(r => r.json()).catch(e => ({ error: String(e) }));
  b.disabled = false; b.textContent = '생성 · 등록';
  if (r.error) { alert('생성 실패:\n' + r.error); return; }
  
  STATE = await (await fetch('/api/state')).json();
  const ts = $('#terrain-sel'); ts.textContent = '';
  STATE.terrains.forEach(t => ts.append(el('option', { value: t.id }, t.id)));
  ts.value = name;
  $('#tgen').classList.remove('on');
  await loadTerrain(); await doDerive();
  alert(`'${name}' 생성 완료\n`
    + `  기복 ${r.relief_m} m (해저면 z ${r.seabed_bottom_m} ~ ${r.seabed_top_m})\n`
    + `  파일 ${r.written.length}개 기록, scene_config.json 등록\n`
    + (r.missing_runtime?.length ? `  [주의] 런타임 폴더 없음: ${r.missing_runtime}\n` : '')
    + `  재빌드/재패킹 없이 바로 취득에 쓸 수 있습니다.`);
}





function assetTargetSize(o) {
  return o?.target_size_m
      ?? STATE.scene.object_categories?.[o?.category]?.target_size_m
      ?? null;
}
function catTargetSize(category) {
  return STATE.scene.object_categories?.[category]?.target_size_m ?? null;
}

function assetStatus(o) {
  if (EXCLUDED.has(o.id)) return ['크기 비율 문제', 'off'];
  if (o.id === 'archaeological_ship/dallido_ship_reconstructed') return ['FBX 미도착 (추후)', 'hold'];
  if (o.id === 'archaeological_ship/dallido_ship_remaining_hull') return ['크기 출처 미확보', 'hold'];
  if (!o.available) return ['재질/음향 물성 미확보', 'hold'];
  return ['사용중', 'ok'];
}

function libRender() {
  const host = $('#lib-list'); host.textContent = '';
  const items = (STATE.catalog || [])
    .filter(o => !LIB.cat || o.category === LIB.cat)
    .filter(o => !LIB.onlyOn || !EXCLUDED.has(o.id));
  items.forEach(o => {
    const off = EXCLUDED.has(o.id);
    const node = el('div', {
      class: 'lib-item' + (off ? ' off' : '') + (LIB.sel === o.id ? ' sel' : ''),
      title: o.source,
      onclick: () => libSelect(o.id),
    },
      el('div', { class: 'nm' }, o.name || o.id.split('/')[1]),
      el('div', { class: 'meta' },
        el('span', {}, o.category),
        el('span', {}, o.measured_max_m ? o.measured_max_m.toFixed(2) + ' m' : '?')),
      assetStatus(o)[1] !== 'ok' ? el('div', { class: 'off-tag' }, assetStatus(o)[0]) : '');
    host.append(node);
  });
  $('#lib-count').textContent =
    `${items.length} / ${STATE.catalog.length} 표시 · 제외 ${EXCLUDED.size}`;
  const sel = STATE.catalog.find(o => o.id === LIB.sel);
  $('#lib-toggle').textContent = sel && EXCLUDED.has(sel.id) ? '씬에 다시 포함' : '씬에서 제외';
  $('#lib-toggle').disabled = !sel;
}

async function libSelect(id) {
  LIB.sel = id; libRender();
  const o = STATE.catalog.find(x => x.id === id);
  const photos = $('#lib-photos');
  photos.textContent = '';
  const size = assetTargetSize(o);
  kv($('#lib-kv'), [['불러오는 중…', '']]);
  const url = '/api/mesh?id=' + encodeURIComponent(id) + (size ? '&size=' + size : '');
  const m = await fetch(url).then(r => r.json()).catch(e => ({ error: String(e) }));
  if (m.error) { kv($('#lib-kv'), [['메쉬 로드 실패', m.error, 'bad']]); V3.obj?.show(null); return; }
  const isWreck = (m.tags || []).includes('wreck');
  V3.obj?.show(m, isWreck ? [0.86, 0.70, 0.32, 1] : [0.78, 0.81, 0.86, 1]);
  V3.obj?.sc.refresh();
  kv($('#lib-kv'), [
    ['ID', id],
    ['카테고리', m.category],
    ['배치 크기 (최대변)', size ? size.toFixed(3) + ' m' : '— (실물 치수 출처 없음)'],
    ['크기 출처', o?.size_source ? `${o.size_source}${o.size_source_page ? ' · ' + o.size_source_page : ''}` : '—'],
    ['크기 신뢰도', o?.size_confidence || '—'],
    ['배치 후 bbox', m.bbox_m.map(v => v.toFixed(2)).join(' × ') + ' m'],
    ['카탈로그 실측', (o?.measured_max_m ?? '—') + ' m',
      Math.abs((o?.measured_max_m ?? 0) - 1.96) < 0.02 ? 'warn' : ''],
    ['원본 파일 최대변', m.source_max_m + ' (파일 단위)',
      (o?.measured_max_m && Math.abs(m.source_max_m / o.measured_max_m - 1) > 0.05)
        ? 'warn' : ''],
    ['프리뷰 삼각형', m.n_faces + ' (원본에서 축약)'],
    ['태그', (m.tags || []).join(', ') || '없음'],
    ['상태', assetStatus(o)[0], assetStatus(o)[1] === 'ok' ? 'ok' : 'bad'],
  ]);
  (o?.preview_paths || []).forEach((path, i) => {
    const image = el('img', {src: '/file?path=' + encodeURIComponent(path), alt: (o.preview_files || [])[i] || '원본 프리뷰'});
    image.title = image.alt;
    image.onerror = () => image.remove();
    photos.append(image);
  });
}

async function libSave() {
  await fetch('/api/save_scene', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scene: { excluded_objects: [...EXCLUDED].sort() } })
  });
  STATE.scene.excluded_objects = [...EXCLUDED].sort();
  flash($('#lib-save'), '적용됨');
}


function initTabs(id, panes) {
  document.querySelectorAll('#' + id + ' button').forEach(b =>
    b.addEventListener('click', () => {
      document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      Object.entries(panes).forEach(([k, sel]) =>
        $(sel).style.display = (k === b.dataset.v) ? '' : 'none');
      
      if (b.dataset.v === '3d') {
        draw3D();
        V3.terr?.sc.refresh();
      }
    }));
}








/* 예전 사용자 정의 sweep 화면은 단일 데이터셋 취득 흐름으로 통합했다.
  const ss = (STATE.scene && STATE.scene.survey_sampling) || {};
  const s = STATE.sensor;
  const box = $('#sw-axes'); if (!box) return;
  box.textContent = '';
  SWEEP_AXES.forEach(([k, label, unit, step]) => {
    const d = ss[k];
    const lo = Array.isArray(d) ? d[0] : (s[k] ?? 0);
    const hi = Array.isArray(d) ? (d[1] ?? d[0]) : (s[k] ?? 0);
    const on = el('input', { type: 'checkbox', class: 'chk', id: 'sw-on-' + k });
    const a = el('input', { type: 'number', id: 'sw-lo-' + k, value: lo, step });
    const b = el('input', { type: 'number', id: 'sw-hi-' + k, value: hi, step });
    const row = el('div', { class: 'sw-ax off' }, on,
      el('div', { class: 'nm' }, label, unit ? el('span', { class: 'u' }, unit) : ''), a, b);
    on.addEventListener('change', () => { row.classList.toggle('off', !on.checked); estSweep(); });
    box.append(row);
  });

  
  const tb = $('#sw-terrains'); tb.textContent = '';
  (STATE.terrains || []).forEach(t => {
    const c = el('input', { type: 'checkbox', class: 'chk', 'data-t': t.id });
    const lab = el('label', {}, c, t.id);
    c.addEventListener('change', () => { lab.classList.toggle('on', c.checked); estSweep(); });
    tb.append(lab);
  });

  
  const ob = $('#sw-objects'); ob.textContent = '';
  const mats = (STATE.materials || []).map(m => m.id);
  const engineMats = ['M_CobbleStone_Rough', 'M_Metal_Steel', 'M_Wood_Pine',
    'M_Brown_Sand', 'ShipwreckProjectAnchorStone', 'ShipwreckProjectReefRock'];
  ob.append(el('div', { class: 'sw-obj', style: 'color:var(--dim);font-size:11px' },
    el('div', {}, '카테고리'), el('div', {}, '최소'), el('div', {}, '최대'), el('div', {}, '재질 후보')));
  Object.entries(STATE.scene.object_categories || {}).forEach(([name, c]) => {
    const [lo, hi] = c.count_range || [0, 0];
    const a = el('input', { type: 'number', id: 'sw-c-lo-' + name, value: lo, step: 1 });
    const b = el('input', { type: 'number', id: 'sw-c-hi-' + name, value: hi, step: 1 });
    const sel = el('select', { id: 'sw-m-' + name, multiple: true, size: 3 });
    engineMats.forEach(m => {
      const o = el('option', { value: m }, m);
      if (m === c.material) o.selected = true;
      sel.append(o);
    });
    sel.title = '두 개 이상 고르면 샘플마다 무작위로 하나를 씁니다 (재질 다양화). '
      + '하나만 고르면 그 재질로 고정됩니다.';
    [a, b].forEach(x => x.addEventListener('input', estSweep));
    ob.append(el('div', { class: 'sw-obj' }, el('div', {}, name), a, b, sel));
  });
  estSweep();
}
*/

async function runLargeDataset(dry) {
  const outputs = {
    waterfall_png: $('#out-waterfall').checked,
    true_aspect_png: $('#out-trueaspect').checked,
    raw_numpy: $('#out-raw-npy').checked,
    processed_numpy: $('#out-proc-npy').checked,
    mask: $('#out-mask').checked,
    bbox: $('#out-bbox').checked,
    bbox_overlay: $('#out-overlay').checked,
    scene_preview: $('#out-preview').checked,
    export_zip: $('#out-zip').checked,
  };
  const selected = Object.entries(outputs).filter(([k, v]) => k !== 'export_zip' && v);
  if (!selected.length) {
    $('#log').textContent = '오류: 저장할 산출물을 하나 이상 선택하세요.';
    return;
  }
  if (outputs.bbox_overlay && !outputs.waterfall_png) {
    $('#log').textContent = '오류: bbox 검수 이미지를 저장하려면 워터폴 PNG가 필요합니다.';
    return;
  }
  await saveSensor();
  const body = {
    name: ($('#lg-name').value || 'westsea_sss').trim(),
    n: parseInt($('#lg-n').value, 10) || 1,
    seed: parseInt($('#lg-seed').value, 10) || 0,
    platform: $('#platform').value,
    floor_clutter_pct: parseFloat($('#lg-floor-pct').value),
    wreck_pct: parseFloat($('#lg-wreck-pct').value),
    wreck_tilt_deg: parseFloat($('#lg-wreck-tilt').value),
    depression_deg: parseFloat($('#lg-depression').value),
    noise_level: parseFloat($('#lg-noise-level').value),
    speckle_strength: parseFloat($('#lg-speckle-strength').value),
    texture_cv: parseFloat($('#lg-texture-cv').value),
    outputs,
    dry_run: !!dry,
  };
  const r = await fetch('/api/dataset', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(r => r.json()).catch(e => ({ error: String(e) }));
  if (r.error) { $('#log').textContent = '오류: ' + r.error; return; }
  JOB = r.job;
  $('#btn-large').disabled = true; $('#btn-large-dry').disabled = true;
  $('#btn-preview').disabled = true;
  $('#btn-stop').disabled = false;
  $('#log').textContent = (dry ? '데이터셋 계획 생성 중…\n'
    : '데이터셋 취득 시작…\n') + '$ ' + (r.cmd || []).join(' ') + '\n';
  $('#result').textContent = '';
  pollJob();
}

function updateOutputWarnings() {
  const host = $('#output-warnings');
  if (!host) return;
  host.textContent = '';
  const notes = [];
  if ($('#out-raw-npy').checked) notes.push('원시 NumPy는 저장 용량이 크게 늘어납니다.');
  if ($('#out-zip').checked) notes.push('ZIP 내보내기는 완료 시 원본 크기만큼의 추가 여유 공간이 필요합니다.');
  if ($('#out-overlay').checked && !$('#out-waterfall').checked)
    notes.push('bbox 검수 이미지는 워터폴 PNG를 함께 선택해야 합니다.');
  if (Number.isFinite(+STATE?.disk_free_gib))
    notes.push(`현재 저장 공간 여유: ${(+STATE.disk_free_gib).toFixed(1)} GiB`);
  if (!notes.length) notes.push('선택한 산출물만 sample 폴더에 저장합니다.');
  notes.forEach(note => host.append(el('li', {}, note)));
}

async function init() {
  STATE = await (await fetch('/api/state')).json();
  EXCLUDED = new Set(STATE.scene.excluded_objects || []);
  buildForms();
  const largeDefaults = STATE.scene?.large_dataset_profile?.user_control_defaults || {};
  if (Number.isFinite(+largeDefaults.floor_clutter_pct))
    $('#lg-floor-pct').value = largeDefaults.floor_clutter_pct;
  if (Number.isFinite(+largeDefaults.wreck_pct))
    $('#lg-wreck-pct').value = largeDefaults.wreck_pct;
  if (Number.isFinite(+largeDefaults.wreck_tilt_deg))
    $('#lg-wreck-tilt').value = largeDefaults.wreck_tilt_deg;
  if (Number.isFinite(+largeDefaults.depression_deg))
    $('#lg-depression').value = largeDefaults.depression_deg;
  if (Number.isFinite(+largeDefaults.noise_level))
    $('#lg-noise-level').value = largeDefaults.noise_level;
  if (Number.isFinite(+largeDefaults.speckle_strength))
    $('#lg-speckle-strength').value = largeDefaults.speckle_strength;
  if (Number.isFinite(+largeDefaults.texture_cv))
    $('#lg-texture-cv').value = largeDefaults.texture_cv;

  
  try {
    V3.terr = new Viz3D.TerrainView($('#cv-terrain'));
    V3.obj = new Viz3D.ObjectView($('#cv-obj'));
    V3.tgen = new Viz3D.TerrainView($('#cv-tgen'));
    if (V3.terr.sc.dead) throw new Error('WebGL 사용 불가');
  } catch (e) {
    console.warn('3D 비활성화:', e);
    V3 = { terr: null, obj: null, tgen: null };
    $('#pane-map3d').style.display = 'none';
    $('#pane-map2d').style.display = '';
  }

  initTabs('map-tabs', { '3d': '#pane-map3d', '2d': '#pane-map2d' });

  await loadTerrain();
  await doDerive();
  loadRuns();

  
  const catSel = $('#lib-cat');
  [...new Set((STATE.catalog || []).map(o => o.category))].sort()
    .forEach(c => catSel.append(el('option', { value: c }, c)));
  catSel.addEventListener('change', () => { LIB.cat = catSel.value; libRender(); });
  $('#lib-only-on').addEventListener('change', e => { LIB.onlyOn = e.target.checked; libRender(); });
  $('#lib-close').addEventListener('click', () => $('#lib').classList.remove('on'));
  $('#lib-save').addEventListener('click', libSave);
  $('#lib-toggle').addEventListener('click', () => {
    if (!LIB.sel) return;
    EXCLUDED.has(LIB.sel) ? EXCLUDED.delete(LIB.sel) : EXCLUDED.add(LIB.sel);
    libRender(); libSelect(LIB.sel);
  });

  document.querySelectorAll('.card > h2').forEach(h =>
    h.addEventListener('click', () => h.parentElement.classList.toggle('collapsed')));
  $('#btn-save').addEventListener('click', saveAll);
  $('#btn-manifest').addEventListener('click', genManifest);
  $('#btn-large').addEventListener('click', () => runLargeDataset(false));
  $('#btn-large-dry').addEventListener('click', () => runLargeDataset(true));
  document.querySelectorAll('.output-grid input').forEach(x =>
    x.addEventListener('change', updateOutputWarnings));
  updateOutputWarnings();
  $('#btn-acq-help').addEventListener('click', () => $('#acq-help').classList.add('on'));
  $('#btn-acq-help-close').addEventListener('click', () => $('#acq-help').classList.remove('on'));
  $('#acq-help').addEventListener('click', e => {
    if (e.target.id === 'acq-help') $('#acq-help').classList.remove('on');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $('#acq-help').classList.remove('on');
  });
  $('#btn-stop').addEventListener('click', async () => {
    await fetch('/api/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: JOB })
    });
  });
  $('#btn-catalog').addEventListener('click', () => {
    $('#lib').classList.add('on');
    libRender();
    V3.obj?.sc.refresh();          
    if (!LIB.sel && STATE.catalog?.length) libSelect(STATE.catalog[0].id);
  });
  $('#modal').addEventListener('click', () => $('#modal').classList.remove('on'));
  document.querySelectorAll('[data-fit]').forEach(b => b.addEventListener('click', () => {
    ({ terr: V3.terr })[b.dataset.fit]?.sc.resetView();
  }));
  $('#btn-preview').addEventListener('click', preview);
  $('#btn-beam').addEventListener('click', e => {
    if (!V3.terr) return;
    V3.terr.showBeam = !V3.terr.showBeam;
    e.target.classList.toggle('on', V3.terr.showBeam);
    e.target.textContent = V3.terr.showBeam ? '센서 빔 끄기' : '센서 빔 보기';
    $('#beam-legend').style.display = V3.terr.showBeam ? '' : 'none';
    draw3D();
  });
  bindTerrainEdit();

  
  tgBuildForm();
  const ps = $('#tg-preset');
  ps.append(el('option', { value: '' }, '프리셋 선택…'));
  Object.keys(STATE.terrain_presets || {}).forEach(k => ps.append(el('option', { value: k }, k)));
  ps.addEventListener('change', () => {
    const pr = STATE.terrain_presets?.[ps.value];
    if (!pr) return;
    
    TG_GROUPS.forEach(([, f]) => f.forEach(([k, , , , dv]) => {
      if ($('#tg-' + k)) $('#tg-' + k).value = (k in pr) ? pr[k] : dv;
    }));
    tgPreview();
  });
  $('#btn-newterrain').addEventListener('click', () => {
    $('#tgen').classList.add('on');
    V3.tgen?.sc.refresh();
    tgPreview();
  });
  $('#tg-close').addEventListener('click', () => $('#tgen').classList.remove('on'));
  $('#tg-create').addEventListener('click', tgCreate);
  $('#platform').addEventListener('change', scheduleDerive);
  $('#n_legs').addEventListener('input', scheduleDerive);
  restoreActiveJob();
}
init();
