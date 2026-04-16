// ── Projection toggle ─────────────────────────────────────────────────────
let currentProjection = 'robinson';

/** D3 geoOrthographic.rotate([λ, φ, γ]) — never touched by Plotly */
let globeRotation = [-20, 0, 0];
let globeZoom = 1;
const GLOBE_ZOOM_MIN = 0.4;
const GLOBE_ZOOM_MAX = 4;
let globeBaseScalePx = 200;

let _worldAtlasTopo = null;
/** Same reference as `_worldAtlasTopo` after first successful world-atlas fetch. */
let globeWorld = null;
/** Cached features array for Globe 1 (avoids repeated topojson.feature() calls). */
let globeWorld1Features = null;

/** ISO3 → normalized highlight weight (0–1) for article hover overlay */
const globeHighlightMap = new Map();

let _inertiaRaf = null;
let _centroidAnimRaf = null;
let _dragLastDx = 0;
let _dragLastDy = 0;
let _globeDragDist = 0;
let _globeWheelZoomTmr = null;
/** Same behavior as Plotly hover: sound only when the hovered country changes, not every mousemove pixel */
let _globeLastHoverIso3 = null;

/** Idle auto-rotation (deg/ms); λ decreases when dir is -1 */
const AUTO_ROTATE_SPEED = 0.012;
let autoRotateDir = -1;
let autoRotateRaf = null;
let autoRotatePaused = false;
let clickPaused = false;
let autoResumeTimer = null;
let _postDragResumeTimer = null;
let postDragAutoHold = false;
let _globeDragActive = false;
let cumulativeDragDeltaX = 0;
let lastAutoRotateTs = 0;

const G = {
  svg: null,
  gClip: null,
  gCountries: null,
  gHighlight: null,
  gGraticule: null,
  oceanCircle: null,
  rimCircle: null,
  projection: null,
  path: null,
  features: [],
  topology: null,
  w: 0,
  h: 0,
  graticuleGeo: null,
  clipCircle: null,
  clipId: null,
};

function cancelGlobeMotion() {
  if (_inertiaRaf) {
    cancelAnimationFrame(_inertiaRaf);
    _inertiaRaf = null;
  }
  if (_centroidAnimRaf) {
    cancelAnimationFrame(_centroidAnimRaf);
    _centroidAnimRaf = null;
  }
}

function stopAutoRotateLoop() {
  if (autoRotateRaf) {
    cancelAnimationFrame(autoRotateRaf);
    autoRotateRaf = null;
  }
}

/** Play/pause control (created once; #globe-playpause on #map-container) */
let globePlayPauseBtn = null;
let ppIcon = null;
let ppLabel = null;
let ppGlyphPlay = null;
let ppGlyphPause = null;

const GLOBE_PP_HINT = 'Press Space to pause animation';

function syncGlobePlayPauseButton() {
  if (!ppLabel || !ppGlyphPlay || !ppGlyphPause || !ppIcon || !globePlayPauseBtn) return;
  const paused = autoRotatePaused || clickPaused;
  if (paused) {
    ppIcon.style.display = 'block';
    ppGlyphPlay.style.display = 'flex';
    ppGlyphPause.style.display = 'none';
    ppLabel.textContent = 'PAUSED';
    ppLabel.style.fontSize = '0.62rem';
    ppLabel.style.letterSpacing = '0.10em';
    ppLabel.style.textTransform = 'uppercase';
    ppLabel.style.maxWidth = 'none';
    ppLabel.style.lineHeight = '1.2';
    ppLabel.style.color = '#8b949e';
    ppIcon.style.width = '96px';
    ppIcon.style.minWidth = '96px';
    ppIcon.style.height = '64px';
    ppGlyphPlay.style.fontSize = '2rem';
    globePlayPauseBtn.title = 'Click or press Space to resume';
    globePlayPauseBtn.setAttribute('aria-label', 'Globe rotation paused. Click or press Space to resume.');
    globePlayPauseBtn.style.pointerEvents = 'auto';
    globePlayPauseBtn.style.cursor = 'pointer';
  } else {
    ppIcon.style.display = 'none';
    ppGlyphPlay.style.display = 'none';
    ppGlyphPause.style.display = 'none';
    ppLabel.textContent = GLOBE_PP_HINT;
    ppLabel.style.fontSize = '0.72rem';
    ppLabel.style.letterSpacing = '0.02em';
    ppLabel.style.textTransform = 'none';
    ppLabel.style.maxWidth = '220px';
    ppLabel.style.lineHeight = '1.35';
    ppLabel.style.color = '#8b949e';
    ppIcon.style.width = '52px';
    ppIcon.style.minWidth = '52px';
    ppIcon.style.height = '38px';
    ppGlyphPlay.style.fontSize = '1.2rem';
    globePlayPauseBtn.title = '';
    globePlayPauseBtn.setAttribute('aria-label', 'Globe is rotating. Press Space to pause.');
    globePlayPauseBtn.style.pointerEvents = 'none';
    globePlayPauseBtn.style.cursor = 'default';
  }
}

function toggleGlobeRotation() {
  autoRotatePaused = !autoRotatePaused;
  if (autoRotatePaused) {
    if (autoRotateRaf) {
      cancelAnimationFrame(autoRotateRaf);
      autoRotateRaf = null;
    }
  } else {
    if (_inertiaRaf) {
      cancelAnimationFrame(_inertiaRaf);
      _inertiaRaf = null;
    }
    tryStartAutoRotate();
  }
  syncGlobePlayPauseButton();
}

/** Used by the paused-state control (click); clears Space pause and click-to-select pause. */
function resumeGlobeAutoRotation() {
  autoRotatePaused = false;
  if (autoResumeTimer) {
    clearTimeout(autoResumeTimer);
    autoResumeTimer = null;
  }
  clickPaused = false;
  if (_inertiaRaf) {
    cancelAnimationFrame(_inertiaRaf);
    _inertiaRaf = null;
  }
  tryStartAutoRotate();
  syncGlobePlayPauseButton();
}

function tryStartAutoRotate() {
  if (currentProjection !== 'globe') return;
  if (autoRotatePaused || clickPaused || _globeDragActive || postDragAutoHold) return;
  if (autoRotateRaf) return;
  if (_inertiaRaf) {
    cancelAnimationFrame(_inertiaRaf);
    _inertiaRaf = null;
  }
  lastAutoRotateTs = performance.now();
  function tick(now) {
    autoRotateRaf = null;
    if (
      currentProjection !== 'globe' ||
      autoRotatePaused ||
      clickPaused ||
      _globeDragActive ||
      postDragAutoHold
    ) {
      return;
    }
    const deltaMs = Math.min(64, now - lastAutoRotateTs);
    lastAutoRotateTs = now;
    globeRotation[0] += autoRotateDir * AUTO_ROTATE_SPEED * deltaMs;
    redrawGlobe();
    if (!autoRotatePaused && !clickPaused && !_globeDragActive && !postDragAutoHold) {
      autoRotateRaf = requestAnimationFrame(tick);
    }
  }
  autoRotateRaf = requestAnimationFrame(tick);
}

function beginClickAutoPause() {
  clickPaused = true;
  if (autoResumeTimer) {
    clearTimeout(autoResumeTimer);
    autoResumeTimer = null;
  }
  if (_postDragResumeTimer) {
    clearTimeout(_postDragResumeTimer);
    _postDragResumeTimer = null;
  }
  postDragAutoHold = false;
  stopAutoRotateLoop();
  autoResumeTimer = setTimeout(() => {
    autoResumeTimer = null;
    clickPaused = false;
    if (!autoRotatePaused) tryStartAutoRotate();
    syncGlobePlayPauseButton();
  }, 2200);
  syncGlobePlayPauseButton();
}

function onGlobeAutoRotateKeydown(ev) {
  if (ev.key !== ' ' && ev.code !== 'Space') return;
  if (ev.repeat) return;
  if (currentProjection !== 'globe') return;
  const t = ev.target;
  if (t && ((t.tagName && t.tagName === 'INPUT') || (t.tagName && t.tagName === 'TEXTAREA') || t.isContentEditable))
    return;
  ev.preventDefault();
  toggleGlobeRotation();
}

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', onGlobeAutoRotateKeydown);

  const _mapCont = document.getElementById('map-container');
  if (_mapCont) {
    globePlayPauseBtn = document.createElement('div');
    globePlayPauseBtn.id = 'globe-playpause';
    globePlayPauseBtn.style.cssText = `
      position: absolute;
      bottom: 52px;
      left: 16px;
      z-index: 10000;
      display: none;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      pointer-events: auto;
      user-select: none;
      cursor: pointer;
    `;

    ppLabel = document.createElement('div');
    ppLabel.id = 'globe-pp-label';
    ppLabel.style.cssText = `
      font-family: 'Literata', Georgia, serif;
      font-size: 0.72rem;
      color: #8b949e;
      letter-spacing: 0.02em;
      text-transform: none;
      text-align: center;
      transition: color 0.2s;
      max-width: 220px;
      line-height: 1.35;
      box-sizing: border-box;
    `;
    ppLabel.textContent = GLOBE_PP_HINT;

    ppIcon = document.createElement('div');
    ppIcon.id = 'globe-pp-icon';
    ppIcon.style.cssText = `
      box-sizing: border-box;
      position: relative;
      width: 52px;
      min-width: 52px;
      height: 38px;
      padding: 0;
      color: #484f58;
      transition: color 0.2s;
      background: rgba(22, 27, 34, 0.98);
      border: 1px solid #30363d;
      border-radius: 4px;
      isolation: isolate;
      contain: layout style paint;
    `;

    const _glyphBase = `
      position: absolute;
      inset: 0;
      align-items: center;
      justify-content: center;
      font-size: 1.2rem;
      font-family: system-ui, 'Segoe UI Symbol', 'Apple Symbols', sans-serif;
      color: inherit;
      line-height: 1;
      pointer-events: none;
    `;
    ppGlyphPause = document.createElement('span');
    ppGlyphPause.setAttribute('aria-hidden', 'true');
    ppGlyphPause.textContent = '❚❚';
    ppGlyphPause.style.cssText = _glyphBase.trim() + '\n      display: flex;\n    ';
    ppGlyphPlay = document.createElement('span');
    ppGlyphPlay.setAttribute('aria-hidden', 'true');
    ppGlyphPlay.textContent = '▶';
    ppGlyphPlay.style.cssText = _glyphBase.trim() + '\n      display: none;\n    ';
    ppIcon.appendChild(ppGlyphPause);
    ppIcon.appendChild(ppGlyphPlay);

    globePlayPauseBtn.appendChild(ppLabel);
    globePlayPauseBtn.appendChild(ppIcon);
    _mapCont.appendChild(globePlayPauseBtn);

    globePlayPauseBtn.addEventListener('click', (ev) => {
      if (!autoRotatePaused && !clickPaused) return;
      ev.preventDefault();
      resumeGlobeAutoRotation();
    });

    globePlayPauseBtn.addEventListener('mouseenter', () => {
      if (!autoRotatePaused && !clickPaused) return;
      ppIcon.style.borderColor = '#8b949e';
      ppLabel.style.color = '#c9d1d9';
      ppIcon.style.color = '#c9d1d9';
    });
    globePlayPauseBtn.addEventListener('mouseleave', () => {
      if (!autoRotatePaused && !clickPaused) return;
      ppIcon.style.borderColor = '#30363d';
      ppLabel.style.color = '#8b949e';
      ppIcon.style.color = '#484f58';
    });

    syncGlobePlayPauseButton();
  }
}

function neIdToIso3(id) {
  if (id == null || id === '') return undefined;
  const k = String(Math.abs(Number(id))).padStart(3, '0');
  return NE_NUM_TO_ISO3[k];
}

/** TopoJSON features may expose NE numeric id on .id or iso_n3 in properties */
function featureToIso3(f) {
  let iso = neIdToIso3(f.id);
  if (iso) return iso;
  const p = f.properties || {};
  const n3 = p.iso_n3 != null ? p.iso_n3 : p.ISO_N3;
  if (n3 != null && String(n3) !== '-99') {
    iso = neIdToIso3(n3);
  }
  return iso;
}

/**
 * Crimea bounding box: lon 32.5–36.7, lat 44.4–46.3.
 * Reassigns Natural Earth Russia (643) geometries whose ring centroid falls
 * in that box to Ukraine (804) so choropleth / clicks use UKR.
 */
function reassignCrimeaToUkraine(topo) {
  const CRIMEA_LON = [32.5, 36.7];
  const CRIMEA_LAT = [44.4, 46.3];
  const RUSSIA_ID = 643;
  const UKRAINE_ID = 804;

  const obj = topo.objects && topo.objects.countries;
  if (!obj || !obj.geometries) return topo;

  const fc = topojson.feature(topo, obj);
  const geoms = obj.geometries;

  fc.features.forEach((f, i) => {
    if (Number(f.id) !== RUSSIA_ID) return;
    const coords =
      f.geometry.type === 'Polygon'
        ? f.geometry.coordinates[0]
        : f.geometry.type === 'MultiPolygon'
          ? f.geometry.coordinates[0][0]
          : null;
    if (!coords || coords.length === 0) return;
    let sumLon = 0;
    let sumLat = 0;
    for (const [lon, lat] of coords) {
      sumLon += lon;
      sumLat += lat;
    }
    const cLon = sumLon / coords.length;
    const cLat = sumLat / coords.length;
    if (
      cLon >= CRIMEA_LON[0] &&
      cLon <= CRIMEA_LON[1] &&
      cLat >= CRIMEA_LAT[0] &&
      cLat <= CRIMEA_LAT[1] &&
      geoms[i]
    ) {
      geoms[i].id = UKRAINE_ID;
    }
  });
  return topo;
}

function _shortestLonDelta(from, to) {
  let d = to - from;
  while (d > 180) d -= 360;
  while (d < -180) d += 360;
  return d;
}

function _normLon180(lon) {
  let x = lon;
  while (x > 180) x -= 360;
  while (x < -180) x += 360;
  return x;
}

function _easeInOutQuad(t) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}

function rgbToHex(r, g, b) {
  const x = (n) => Math.max(0, Math.min(255, n | 0)).toString(16).padStart(2, '0');
  return '#' + x(r) + x(g) + x(b);
}

function lerpColor(hexA, hexB, t) {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  return rgbToHex(
    a.r + (b.r - a.r) * t,
    a.g + (b.g - a.g) * t,
    a.b + (b.b - a.b) * t
  );
}

function lerpHex(hexA, hexB, t) {
  return lerpColor(hexA, hexB, t);
}

/** +25% per RGB channel for globe choropleth only (stops unchanged in PUB_META) */
function brightenHex25(hex) {
  const { r, g, b } = hexToRgb(hex);
  return rgbToHex(
    Math.min(255, r * 1.25),
    Math.min(255, g * 1.25),
    Math.min(255, b * 1.25)
  );
}

function colorForCount(count, zmax, stops) {
  if (!count || count < 1) return '#1e2535';
  const t = Math.min(1, Math.max(0, count / zmax));
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i][0];
    const b = stops[i + 1][0];
    if (t >= a && t <= b) {
      const u = b === a ? 0 : (t - a) / (b - a);
      return lerpHex(stops[i][1], stops[i + 1][1], u);
    }
  }
  return stops[stops.length - 1][1];
}

function getGlobeCountryFill(count, zmax, stops) {
  const brightStops = stops.map(([pos, h]) => [pos, brightenHex25(h)]);
  return colorForCount(count, zmax, brightStops);
}

function buildCountMap() {
  const chart = DATASETS[currentPub].chart;
  const m = {};
  for (let i = 0; i < chart.iso3.length; i++) m[chart.iso3[i]] = chart.count[i];
  return m;
}

function currentPubMaxCount() {
  if (!currentPub || !DATASETS[currentPub] || !DATASETS[currentPub].chart) return 1;
  const counts = DATASETS[currentPub].chart.count;
  if (!counts || counts.length === 0) return 1;
  return Math.max(...counts, 1);
}

function updateGlobeLegend() {
  const meta = PUB_META[currentPub];
  const el = document.getElementById('globe-legend-max');
  if (el) el.textContent = String(currentPubMaxCount());
  const cvs = document.getElementById('globe-legend-canvas');
  if (!cvs) return;
  const ctx = cvs.getContext('2d');
  const w = cvs.width;
  const h = cvs.height;
  const stops = meta.colorscale;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  for (let i = 0; i < stops.length; i++) {
    grad.addColorStop(1 - stops[i][0], brightenHex25(stops[i][1]));
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
}

function updateGlobeLegendSize() {
  const mc = document.getElementById('map-container');
  const cvs = document.getElementById('globe-legend-canvas');
  if (!mc || !cvs) return;
  cvs.width = 12;
  cvs.height = Math.max(2, Math.round(mc.clientHeight * 0.5));
  updateGlobeLegend();
}

function redrawGlobe() {
  if (!G.projection || !G.path || !G.gCountries) return;
  G.projection.rotate(globeRotation).scale(globeBaseScalePx * globeZoom);
  const sc = G.projection.scale();
  const tx = G.w / 2;
  const ty = G.h / 2;
  G.projection.translate([tx, ty]);
  if (G.oceanCircle) {
    G.oceanCircle.attr('cx', tx).attr('cy', ty).attr('r', sc);
  }
  if (G.rimCircle) {
    G.rimCircle.attr('cx', tx).attr('cy', ty).attr('r', sc);
  }
  if (G.clipCircle) G.clipCircle.attr('cx', tx).attr('cy', ty).attr('r', sc);

  const counts = buildCountMap();
  const zmax = Math.max(...DATASETS[currentPub].chart.count, 1);
  const stops = PUB_META[currentPub].colorscale;

  if (G.graticuleGeo) {
    G.gGraticule.select('path').attr('d', G.path(G.graticuleGeo));
  }
  G.gCountries.selectAll('path.country').attr('d', G.path).attr('fill', function (d) {
    const iso = featureToIso3(d);
    const c = iso ? counts[iso] : 0;
    return getGlobeCountryFill(c, zmax, stops);
  });

  G.gHighlight.selectAll('*').remove();
  globeHighlightMap.forEach((normW, iso3) => {
    const fillOp = 0.2 + 0.45 * normW;
    G.features.forEach((f) => {
      if (featureToIso3(f) === iso3) {
        G.gHighlight
          .append('path')
          .datum(f)
          .attr('class', 'globe-highlight')
          .attr('d', G.path)
          .attr('fill', 'rgb(255,230,0)')
          .attr('fill-opacity', fillOp)
          .attr('stroke', '#ffe033')
          .attr('stroke-width', 2);
      }
    });
  });
}

function resizeGlobe() {
  if (currentProjection !== 'globe' || !G.svg) return;
  initGlobe(true);
}

/**
 * Builds (or rebuilds) the globe SVG using whatever features are in G.features.
 * Called by initGlobe() after its data is ready,
 * and by resizeGlobe() via the respective init function.
 */
function buildGlobeSVG(isResize) {
  const container = document.getElementById('globe-container');
  if (!container) return;

  const w = G.w;
  const h = G.h;
  const sc0 = globeBaseScalePx * globeZoom;

  const crt = document.getElementById('globe-crt-overlay');
  container.innerHTML = '';
  if (crt) container.appendChild(crt);

  const svg = d3
    .select(container)
    .append('svg')
    .attr('class', 'globe-main-svg')
    .attr('width', w)
    .attr('height', h)
    .style('overflow', 'visible');

  const defs = svg.append('defs');
  const gradId = 'globe-ocean-grad';
  const rg = defs
    .append('radialGradient')
    .attr('id', gradId)
    .attr('cx', '50%')
    .attr('cy', '50%')
    .attr('r', '50%');
  rg.append('stop').attr('offset', '0%').attr('stop-color', '#1a2340');
  rg.append('stop').attr('offset', '100%').attr('stop-color', '#0a0e17');

  const clipId = 'globe-clip-' + w + 'x' + h;
  G.clipId = clipId;
  G.clipCircle = defs
    .append('clipPath')
    .attr('id', clipId)
    .append('circle')
    .attr('cx', w / 2)
    .attr('cy', h / 2)
    .attr('r', sc0);

  const projection = d3.geoOrthographic().scale(sc0).translate([w / 2, h / 2]).rotate(globeRotation).clipAngle(90);
  const path = d3.geoPath(projection);
  G.projection = projection;
  G.path = path;
  G.svg = svg;

  G.oceanCircle = svg
    .append('circle')
    .attr('cx', w / 2)
    .attr('cy', h / 2)
    .attr('r', sc0)
    .attr('fill', 'url(#' + gradId + ')');

  const graticule = d3.geoGraticule().step([15, 15])();
  G.graticuleGeo = graticule;

  G.gClip = svg.append('g').attr('class', 'globe-clipped').attr('clip-path', 'url(#' + clipId + ')');

  G.gGraticule = G.gClip.append('g').attr('class', 'graticule');
  G.gGraticule
    .append('path')
    .datum(graticule)
    .attr('fill', 'none')
    .attr('stroke', '#1e2030')
    .attr('stroke-width', 0.4)
    .attr('d', path);

  G.gCountries = G.gClip.append('g').attr('class', 'countries');
  G.gCountries
    .selectAll('path.country')
    .data(G.features)
    .join('path')
    .attr('class', 'country')
    .attr('stroke', '#21262d')
    .attr('stroke-width', 0.5)
    .attr('vector-effect', 'non-scaling-stroke')
    .each(function (d) {
      d3.select(this).attr('d', path);
    });

  G.gHighlight = G.gClip.append('g').attr('class', 'highlight-layer');

  G.rimCircle = svg
    .append('circle')
    .attr('cx', w / 2)
    .attr('cy', h / 2)
    .attr('r', sc0)
    .attr('fill', 'none')
    .attr('stroke', 'rgba(200,210,255,0.25)')
    .attr('stroke-width', 1.2);

  const drag = d3
    .drag()
    .on('start', () => {
      cancelGlobeMotion();
      stopAutoRotateLoop();
      if (_postDragResumeTimer) {
        clearTimeout(_postDragResumeTimer);
        _postDragResumeTimer = null;
      }
      postDragAutoHold = false;
      _globeDragActive = true;
      cumulativeDragDeltaX = 0;
      _globeDragDist = 0;
    })
    .on('drag', (event) => {
      const k = 0.28;
      cumulativeDragDeltaX += event.dx;
      _globeDragDist += Math.abs(event.dx) + Math.abs(event.dy);
      globeRotation[0] += event.dx * k;
      globeRotation[1] -= event.dy * k;
      globeRotation[1] = Math.max(-90, Math.min(90, globeRotation[1]));
      _dragLastDx = event.dx;
      _dragLastDy = event.dy;
      redrawGlobe();
    })
    .on('end', () => {
      _globeDragActive = false;
      const dxSum = cumulativeDragDeltaX;
      cumulativeDragDeltaX = 0;
      if (dxSum > 60) autoRotateDir = 1;
      else if (dxSum < -60) autoRotateDir = -1;

      postDragAutoHold = true;
      if (_postDragResumeTimer) {
        clearTimeout(_postDragResumeTimer);
        _postDragResumeTimer = null;
      }
      _postDragResumeTimer = setTimeout(() => {
        _postDragResumeTimer = null;
        postDragAutoHold = false;
        if (!autoRotatePaused) tryStartAutoRotate();
      }, 900);

      let vx = _dragLastDx * 0.28;
      let vy = -_dragLastDy * 0.28;
      function loop() {
        vx *= 0.94;
        vy *= 0.94;
        if (Math.abs(vx) < 0.04 && Math.abs(vy) < 0.04) {
          _inertiaRaf = null;
          return;
        }
        globeRotation[0] += vx;
        globeRotation[1] += vy;
        globeRotation[1] = Math.max(-90, Math.min(90, globeRotation[1]));
        redrawGlobe();
        _inertiaRaf = requestAnimationFrame(loop);
      }
      if (Math.hypot(vx, vy) > 0.15) _inertiaRaf = requestAnimationFrame(loop);
    });

  svg.call(drag);

  svg.on('click', (ev) => {
    if (_globeDragDist > 4) return;
    const [x, y] = d3.pointer(ev, svg.node());
    const coord = G.projection.invert([x, y]);
    if (!coord) return;
    let hit = null;
    for (let i = 0; i < G.features.length; i++) {
      if (d3.geoContains(G.features[i], coord)) {
        hit = G.features[i];
        break;
      }
    }
    if (!hit) return;
    const iso3 = featureToIso3(hit);
    if (!iso3) return;
    populateSidebar(iso3);
    playCRTClick();
    triggerClickRipple(ev.clientX, ev.clientY);
    beginClickAutoPause();
    animateGlobeToIso3(iso3);
  });

  svg.on('mousemove', (ev) => {
    const [x, y] = d3.pointer(ev, svg.node());
    const coord = G.projection.invert([x, y]);
    if (!coord) {
      _globeLastHoverIso3 = null;
      hideTooltip();
      return;
    }
    let hit = null;
    for (let i = 0; i < G.features.length; i++) {
      if (d3.geoContains(G.features[i], coord)) {
        hit = G.features[i];
        break;
      }
    }
    if (!hit) {
      _globeLastHoverIso3 = null;
      hideTooltip();
      return;
    }
    const iso3 = featureToIso3(hit);
    if (!iso3) {
      _globeLastHoverIso3 = null;
      hideTooltip();
      return;
    }
    const chart = DATASETS[currentPub].chart;
    const idx = chart.iso3.indexOf(iso3);
    if (idx < 0) {
      _globeLastHoverIso3 = null;
      hideTooltip();
      return;
    }
    const html = chart.hover[idx];
    if (iso3 === _globeLastHoverIso3) {
      positionTooltip(ev);
    } else {
      _globeLastHoverIso3 = iso3;
      showTooltip(ev, html);
    }
  });

  svg.on('mouseleave', () => {
    _globeLastHoverIso3 = null;
    hideTooltip();
  });

  svg.on(
    'wheel',
    (ev) => {
      ev.preventDefault();
      globeZoom *= ev.deltaY > 0 ? 0.92 : 1.08;
      globeZoom = Math.max(GLOBE_ZOOM_MIN, Math.min(GLOBE_ZOOM_MAX, globeZoom));
      startCameraZoom(ev.deltaY);
      if (_globeWheelZoomTmr) clearTimeout(_globeWheelZoomTmr);
      _globeWheelZoomTmr = setTimeout(() => {
        _globeWheelZoomTmr = null;
        if (typeof stopCameraZoom === 'function') stopCameraZoom();
      }, 300);
      redrawGlobe();
    },
    { passive: false }
  );

  redrawGlobe();
  updateGlobeLegendSize();
  if (!isResize) globeHighlightMap.clear();
  syncGlobePlayPauseButton();
  tryStartAutoRotate();
}

/** Globe 1: world-atlas 110m TopoJSON via CDN */
async function initGlobe(isResize) {
  const container = document.getElementById('globe-container');
  if (!container) return;
  cancelGlobeMotion();
  stopAutoRotateLoop();
  if (_postDragResumeTimer) {
    clearTimeout(_postDragResumeTimer);
    _postDragResumeTimer = null;
  }
  postDragAutoHold = false;
  _globeDragActive = false;
  cumulativeDragDeltaX = 0;
  _globeLastHoverIso3 = null;

  const rect = container.getBoundingClientRect();
  G.w = Math.max(2, Math.round(rect.width));
  G.h = Math.max(2, Math.round(rect.height));
  globeBaseScalePx = Math.min(G.w, G.h) * 0.45;

  if (!_worldAtlasTopo) {
    const res = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    if (!res.ok) throw new Error('world-atlas fetch failed');
    const topo = await res.json();
    _worldAtlasTopo = globeWorld = reassignCrimeaToUkraine(topo);
  }
  const topology = _worldAtlasTopo;
  const countries = topojson.feature(topology, topology.objects.countries);
  G.topology = topology;
  globeWorld1Features = countries.features;
  G.features = globeWorld1Features;

  buildGlobeSVG(isResize);
}


function animateGlobeToIso3(iso3) {
  const c = ISO3_CENTROID[iso3];
  if (!c || c.length < 2) return;
  const clon = _normLon180(c[0]);
  const clat = Math.max(-85, Math.min(85, c[1]));
  const endRot = [-clon, -clat, 0];
  const startRot = globeRotation.slice();
  const d0 = _shortestLonDelta(startRot[0], endRot[0]);
  const d1 = endRot[1] - startRot[1];
  const d2 = endRot[2] - startRot[2];
  cancelGlobeMotion();
  stopAutoRotateLoop();
  const t0 = performance.now();
  const dur = 620;
  function frame(now) {
    const u = Math.min(1, (now - t0) / dur);
    const e = _easeInOutQuad(u);
    globeRotation[0] = startRot[0] + d0 * e;
    globeRotation[1] = startRot[1] + d1 * e;
    globeRotation[2] = startRot[2] + d2 * e;
    globeRotation[1] = Math.max(-90, Math.min(90, globeRotation[1]));
    redrawGlobe();
    if (u < 1) _centroidAnimRaf = requestAnimationFrame(frame);
    else {
      _centroidAnimRaf = null;
      tryStartAutoRotate();
    }
  }
  _centroidAnimRaf = requestAnimationFrame(frame);
}

function redrawGlobeChoropleth() {
  _globeLastHoverIso3 = null;
  if (currentProjection === 'globe' && G.gCountries) redrawGlobe();
  updateGlobeLegendSize();
}

function setProjection(proj) {
  if (!plotlyInited || proj === currentProjection) return;
  if (currentProjection === 'globe' && proj !== 'globe') {
    cancelGlobeMotion();
    stopAutoRotateLoop();
    if (autoResumeTimer) {
      clearTimeout(autoResumeTimer);
      autoResumeTimer = null;
    }
    clickPaused = false;
    if (_postDragResumeTimer) {
      clearTimeout(_postDragResumeTimer);
      _postDragResumeTimer = null;
    }
    postDragAutoHold = false;
    _globeDragActive = false;
  }
  currentProjection = proj;

  document.getElementById('proj-flat').classList.toggle('active', proj === 'robinson');
  document.getElementById('proj-globe').classList.toggle('active', proj === 'globe');

  const plotEl = document.getElementById('plotly-map');
  const globeBox = document.getElementById('globe-container');
  const leg = document.getElementById('globe-legend');

  if (proj === 'globe') {
    if (globePlayPauseBtn) globePlayPauseBtn.style.display = 'flex';
    plotEl.style.display = 'none';
    plotEl.style.pointerEvents = 'none';
    globeBox.classList.add('globe-visible');
    globeBox.setAttribute('aria-hidden', 'false');
    leg.classList.add('globe-visible');
    leg.setAttribute('aria-hidden', 'false');
    initGlobe(false).catch((err) => console.error('[globe]', err));
  } else {
    if (globePlayPauseBtn) globePlayPauseBtn.style.display = 'none';
    globeBox.classList.remove('globe-visible');
    globeBox.setAttribute('aria-hidden', 'true');
    leg.classList.remove('globe-visible');
    leg.setAttribute('aria-hidden', 'true');
    plotEl.style.display = 'block';
    plotEl.style.pointerEvents = 'auto';
    const relBase = {
      dragmode: 'zoom',
      'geo.domain': { x: [0, 1], y: [0, 1] },
      'geo.lonaxis.fixedrange': false,
      'geo.lataxis.fixedrange': false,
      'geo.projection.type': 'robinson',
      'geo.projection.rotation': {},
      'geo.showframe': false,
      'geo.framewidth': 0,
      'geo.lonaxis.showgrid': false,
      'geo.lataxis.showgrid': false,
    };
    Plotly.relayout('plotly-map', relBase);
    requestAnimationFrame(() => {
      Plotly.relayout(plotEl, {
        autosize: true,
        margin: MAP_MARGIN,
        'geo.domain': GEO_DOMAIN_FULL,
        'geo.showframe': false,
        'geo.framewidth': 0,
      }).then(() => Plotly.Plots.resize(plotEl));
    });
    setTimeout(() => Plotly.Plots.resize(document.getElementById('plotly-map')), 0);
  }
}

if (typeof window !== 'undefined' && !window.__globeLegendResizeBound) {
  window.__globeLegendResizeBound = true;
  window.addEventListener('resize', () => {
    if (currentProjection === 'globe') updateGlobeLegendSize();
  });
}
