// SVG chart primitives.
//
// Rules baked in here rather than left to each call site:
//   * thin marks, hairline solid grid, no dashed axes
//   * 4px rounded data-ends anchored to the baseline (square at the baseline)
//   * a 2px surface gap between adjacent fills
//   * selective direct labels -- never a number on every point
//   * a hover layer on every form, with hit targets larger than the mark
//   * a legend whenever there are two or more series, none for one
//   * every chart ships a table view twin, which is also the relief for the
//     three light-mode palette slots that sit under 3:1 contrast
//
// Colors are read from CSS custom properties so light/dark swap in one place.

const NS = 'http://www.w3.org/2000/svg';

export const SERIES = (n) => `var(--series-${((n - 1) % 8) + 1})`;

// Position -> a fixed categorical slot. Fixed, so a filter that removes every
// tight end never repaints the receivers. Six slots is inside the adjacent-pair
// gate the palette validator passes; these are only ever used on bar forms,
// where the pairlist is adjacent.
export const POSITION_SLOT = { QB: 1, RB: 2, WR: 3, TE: 4, K: 5, DEF: 6 };
export const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF'];
export const posColor = (pos) => SERIES(POSITION_SLOT[pos] || 7);

/** Legend for any chart that encodes position as hue. Colour is never alone. */
export const positionLegend = (rows, key = 'position') => {
  const present = new Set(rows.map((r) => r[key]).filter(Boolean));
  return POSITION_ORDER.filter((p) => present.has(p)).map((p) => ({
    label: p, color: posColor(p),
  }));
};

const el = (tag, attrs = {}, parent = null) => {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  if (parent) parent.appendChild(node);
  return node;
};

const html = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const fmt = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(digits);

// ------------------------------------------------------------------ tooltip

let tipEl = null;
function tooltip() {
  if (!tipEl) {
    tipEl = html('div', 'chart-tooltip');
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function showTip(evt, title, rows) {
  const t = tooltip();
  t.innerHTML = '';
  t.appendChild(html('div', 'tt-title', title));
  for (const [label, value] of rows) {
    const r = html('div', 'tt-row');
    r.appendChild(html('span', null, label));
    const b = document.createElement('b');
    b.textContent = value;
    r.appendChild(b);
    t.appendChild(r);
  }
  t.classList.add('show');
  moveTip(evt);
}

function moveTip(evt) {
  const t = tooltip();
  const pad = 14;
  const rect = t.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - pad;
  t.style.left = `${Math.max(8, x)}px`;
  t.style.top = `${Math.max(8, y)}px`;
}

export function hideTip() {
  tooltip().classList.remove('show');
}

function attachTip(node, title, rows) {
  node.addEventListener('mouseenter', (e) => showTip(e, title, rows));
  node.addEventListener('mousemove', moveTip);
  node.addEventListener('mouseleave', hideTip);
  // Keyboard focus shows the same thing hover does.
  node.setAttribute('tabindex', '0');
  node.addEventListener('focus', (e) => {
    const r = node.getBoundingClientRect();
    showTip({ clientX: r.left + r.width / 2, clientY: r.top }, title, rows);
  });
  node.addEventListener('blur', hideTip);
}

// -------------------------------------------------------------- geometry

/** Rounded on the data end only; square where it meets the baseline. */
function barPath(x, y, w, h, r, dir) {
  const rad = Math.max(0, Math.min(r, dir === 'h' ? w : h, (dir === 'h' ? h : w) / 2));
  if (dir === 'h') {
    return `M${x},${y} H${x + w - rad} Q${x + w},${y} ${x + w},${y + rad}
            V${y + h - rad} Q${x + w},${y + h} ${x + w - rad},${y + h} H${x} Z`;
  }
  return `M${x},${y + h} V${y + rad} Q${x},${y} ${x + rad},${y}
          H${x + w - rad} Q${x + w},${y} ${x + w},${y + rad} V${y + h} Z`;
}

/** Mirrored: rounded on the left (negative) end. */
function barPathLeft(x, y, w, h, r) {
  const rad = Math.max(0, Math.min(r, w, h / 2));
  return `M${x + w},${y} H${x + rad} Q${x},${y} ${x},${y + rad}
          V${y + h - rad} Q${x},${y + h} ${x + rad},${y + h} H${x + w} Z`;
}

// ---------------------------------------------------------------- figure

/**
 * Wraps a chart in the shared shell: optional legend, the chart itself, and a
 * table view twin behind a toggle. The table is not a nicety -- it is how a
 * value stays reachable when color alone would not carry it.
 */
export function figure({ svg, legend, table, note, caption }) {
  const wrap = html('div', 'figure');

  if (legend?.length > 1) {
    const box = html('div', 'legend');
    for (const item of legend) {
      const li = html('div', 'legend-item');
      const sw = html('span', `legend-swatch${item.line ? ' line' : ''}`);
      sw.style.background = item.color;
      li.appendChild(sw);
      li.appendChild(html('span', null, item.label));
      box.appendChild(li);
    }
    wrap.appendChild(box);
  }

  const chartBox = html('div', 'chart-view');
  chartBox.appendChild(svg);
  wrap.appendChild(chartBox);

  if (table) {
    const tableBox = html('div', 'table-view hidden');
    tableBox.appendChild(table);
    wrap.appendChild(tableBox);

    const bar = html('div', 'row', '');
    bar.style.cssText = 'justify-content:flex-end;margin-top:8px;gap:4px';
    const toggle = html('button', 'btn ghost sm', 'Table view');
    toggle.addEventListener('click', () => {
      const showingTable = !tableBox.classList.contains('hidden');
      tableBox.classList.toggle('hidden', !showingTable ? false : true);
      chartBox.classList.toggle('hidden', showingTable ? false : true);
      toggle.textContent = showingTable ? 'Table view' : 'Chart view';
      hideTip();
    });
    bar.appendChild(toggle);
    wrap.appendChild(bar);
  }

  if (caption) wrap.appendChild(html('div', 'small secondary', caption));
  if (note) wrap.appendChild(html('div', 'tiny muted', note));
  return wrap;
}

function dataTable(columns, rows) {
  const t = html('table', 'data');
  const thead = html('thead');
  const tr = html('tr');
  for (const c of columns) {
    const th = html('th', c.num ? 'num' : null, c.label);
    tr.appendChild(th);
  }
  thead.appendChild(tr);
  t.appendChild(thead);
  const tb = html('tbody');
  for (const row of rows) {
    const r = html('tr');
    columns.forEach((c, i) => r.appendChild(html('td', c.num ? 'num' : null, row[i])));
    tb.appendChild(r);
  }
  t.appendChild(tb);
  const scroll = html('div', 'table-scroll');
  scroll.appendChild(t);
  return scroll;
}

// --------------------------------------------------------- horizontal bars

/**
 * One series, one color. A value ramp across nominal categories would
 * double-encode length as hue, so every bar is slot 1 unless the caller
 * supplies an explicit per-item color for a reason other than magnitude.
 */
export function barChart(data, {
  height = null,
  barHeight = 22,
  gap = 8,
  labelWidth = 172,
  valueFormat = (v) => fmt(v, 1),
  color = SERIES(1),
  colorFor = null,
  labelEvery = 1,
  tooltipRows = null,
  xTitle = '',
  legend = null,
} = {}) {
  const rows = data.filter((d) => d.value !== null && d.value !== undefined);
  const rowH = barHeight + gap;
  const padTop = 6;
  const axisBand = xTitle ? 34 : 22;
  const h = height || padTop + rows.length * rowH + axisBand;
  const w = 720;
  const plotLeft = labelWidth;
  const plotRight = w - 58;
  const plotW = Math.max(40, plotRight - plotLeft);

  const max = Math.max(1, ...rows.map((d) => Math.abs(d.value)));
  const scale = (v) => (Math.abs(v) / max) * plotW;

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${w} ${h}`,
    preserveAspectRatio: 'xMinYMin meet', role: 'img',
    style: `max-height:${h}px`,
  });

  // Recessive solid hairline grid.
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const x = plotLeft + (plotW * i) / ticks;
    el('line', { class: 'grid-line', x1: x, y1: padTop, x2: x, y2: padTop + rows.length * rowH }, svg);
    el('text', {
      class: 'tick', x, y: padTop + rows.length * rowH + 14, 'text-anchor': 'middle',
    }, svg).textContent = valueFormat((max * i) / ticks);
  }
  el('line', {
    class: 'axis-line', x1: plotLeft, y1: padTop, x2: plotLeft, y2: padTop + rows.length * rowH,
  }, svg);
  if (xTitle) {
    el('text', {
      class: 'axis-title', x: plotLeft + plotW / 2, y: h - 4, 'text-anchor': 'middle',
    }, svg).textContent = xTitle;
  }

  rows.forEach((d, i) => {
    const y = padTop + i * rowH;
    const bw = Math.max(2, scale(d.value));
    const fill = d.color || (colorFor ? colorFor(d) : color);

    const label = el('text', {
      class: 'mark-label', x: plotLeft - 10, y: y + barHeight / 2 + 4, 'text-anchor': 'end',
      fill: 'var(--text-primary)',
    }, svg);
    label.textContent = d.label.length > 26 ? `${d.label.slice(0, 25)}…` : d.label;

    // The 2px gap is achieved by insetting the bar, not by drawing a border.
    el('path', {
      class: 'mark', d: barPath(plotLeft, y + 1, bw, barHeight - 2, 4, 'h'), fill,
    }, svg);

    if (i % labelEvery === 0) {
      el('text', {
        class: 'mark-label strong', x: plotLeft + bw + 7, y: y + barHeight / 2 + 4,
      }, svg).textContent = valueFormat(d.value);
    }

    // Hit target spans the full row, comfortably larger than the mark.
    const hit = el('rect', {
      class: 'hit', x: 0, y, width: w, height: rowH,
      role: 'graphics-symbol', 'aria-label': `${d.label}: ${valueFormat(d.value)}`,
    }, svg);
    attachTip(hit, d.label, tooltipRows ? tooltipRows(d) : [[xTitle || 'Value', valueFormat(d.value)]]);
  });

  return figure({
    svg,
    legend,
    table: dataTable(
      [{ label: 'Item' }, { label: xTitle || 'Value', num: true }],
      rows.map((d) => [d.label, valueFormat(d.value)])
    ),
  });
}

// -------------------------------------------------------- diverging bars

/**
 * Two poles that read as opposite, neutral gray at zero. Used for "my edge vs
 * theirs" -- a genuine polarity, which is the only thing a diverging scale is
 * for.
 */
export function divergingBar(data, {
  barHeight = 24, gap = 9, labelWidth = 96,
  posLabel = 'You', negLabel = 'Opponent',
  valueFormat = (v) => `${v > 0 ? '+' : ''}${fmt(v, 1)}`,
} = {}) {
  const rows = data;
  const rowH = barHeight + gap;
  const padTop = 6;
  const h = padTop + rows.length * rowH + 26;
  const w = 720;
  const mid = labelWidth + (w - labelWidth - 60) / 2;
  const half = (w - labelWidth - 60) / 2;
  const max = Math.max(1, ...rows.map((d) => Math.abs(d.value)));

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMinYMin meet',
    role: 'img', style: `max-height:${h}px`,
  });

  for (const f of [-1, -0.5, 0.5, 1]) {
    const x = mid + f * half;
    el('line', { class: 'grid-line', x1: x, y1: padTop, x2: x, y2: padTop + rows.length * rowH }, svg);
    el('text', { class: 'tick', x, y: padTop + rows.length * rowH + 15, 'text-anchor': 'middle' }, svg)
      .textContent = fmt(Math.abs(f) * max, 0);
  }
  el('line', {
    class: 'axis-line', x1: mid, y1: padTop - 2, x2: mid, y2: padTop + rows.length * rowH + 2,
  }, svg);

  rows.forEach((d, i) => {
    const y = padTop + i * rowH;
    const len = Math.max(2, (Math.abs(d.value) / max) * half);
    const positive = d.value >= 0;
    const fill = positive ? 'var(--div-pos)' : 'var(--div-neg)';

    el('text', {
      class: 'mark-label', x: 8, y: y + barHeight / 2 + 4, fill: 'var(--text-primary)',
    }, svg).textContent = d.label;

    el('path', {
      class: 'mark',
      d: positive
        ? barPath(mid + 1, y + 1, len, barHeight - 2, 4, 'h')
        : barPathLeft(mid - len - 1, y + 1, len, barHeight - 2, 4),
      fill,
    }, svg);

    el('text', {
      class: 'mark-label strong',
      x: positive ? mid + len + 8 : mid - len - 8,
      y: y + barHeight / 2 + 4,
      'text-anchor': positive ? 'start' : 'end',
    }, svg).textContent = valueFormat(d.value);

    const hit = el('rect', {
      class: 'hit', x: 0, y, width: w, height: rowH,
      role: 'graphics-symbol', 'aria-label': `${d.label}: ${valueFormat(d.value)}`,
    }, svg);
    attachTip(hit, d.label, d.rows || [['Edge', valueFormat(d.value)]]);
  });

  return figure({
    svg,
    legend: [
      { label: `${posLabel} ahead`, color: 'var(--div-pos)' },
      { label: `${negLabel} ahead`, color: 'var(--div-neg)' },
    ],
    table: dataTable(
      [{ label: 'Slot' }, { label: 'Edge', num: true }],
      rows.map((d) => [d.label, valueFormat(d.value)])
    ),
  });
}

// -------------------------------------------------------------- line chart

/** Weekly series with a crosshair. Byes render as gaps, not as zeros. */
export function lineChart(series, {
  height = 240, xLabels = [], yTitle = '', markers = [], annotate = [],
} = {}) {
  const w = 760;
  const padL = 44, padR = 18, padT = 12, padB = 34;
  const plotW = w - padL - padR;
  const plotH = height - padT - padB;

  const allX = series.flatMap((s) => s.points.map((p) => p.x));
  const allY = series.flatMap((s) => s.points.filter((p) => p.y !== null).map((p) => p.y));
  const xMin = Math.min(...allX), xMax = Math.max(...allX);
  const yMax = Math.max(1, ...allY) * 1.12;
  const sx = (x) => padL + ((x - xMin) / Math.max(1, xMax - xMin)) * plotW;
  const sy = (y) => padT + plotH - (y / yMax) * plotH;

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${w} ${height}`, preserveAspectRatio: 'xMinYMin meet',
    role: 'img', style: `max-height:${height}px`,
  });

  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i) / 4;
    el('line', { class: 'grid-line', x1: padL, y1: y, x2: w - padR, y2: y }, svg);
    el('text', { class: 'tick', x: padL - 8, y: y + 3.5, 'text-anchor': 'end' }, svg)
      .textContent = fmt(yMax * (1 - i / 4), 0);
  }
  el('line', { class: 'axis-line', x1: padL, y1: padT + plotH, x2: w - padR, y2: padT + plotH }, svg);

  (xLabels.length ? xLabels : allX).forEach((x) => {
    const v = typeof x === 'object' ? x.x : x;
    const label = typeof x === 'object' ? x.label : String(x);
    el('text', { class: 'tick', x: sx(v), y: height - 12, 'text-anchor': 'middle' }, svg).textContent = label;
  });
  if (yTitle) {
    el('text', {
      class: 'axis-title', x: padL, y: padT - 2, 'text-anchor': 'start',
    }, svg).textContent = yTitle;
  }

  // Shade the fantasy playoffs rather than drawing a dashed rule for them.
  for (const m of markers) {
    const x1 = sx(m.from), x2 = sx(m.to);
    el('rect', {
      x: x1, y: padT, width: Math.max(2, x2 - x1), height: plotH,
      fill: 'var(--text-primary)', opacity: 0.045,
    }, svg);
    el('text', {
      class: 'tick', x: (x1 + x2) / 2, y: padT + 11, 'text-anchor': 'middle',
    }, svg).textContent = m.label;
  }

  series.forEach((s, si) => {
    const color = s.color || SERIES(si + 1);
    // Break the path at nulls so a bye is a gap, not a dive to zero.
    let d = '';
    let pen = false;
    for (const p of s.points) {
      if (p.y === null || p.y === undefined) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${sx(p.x)},${sy(p.y)} `;
      pen = true;
    }
    el('path', { class: 'mark', d: d.trim(), fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);

    for (const p of s.points) {
      if (p.y === null || p.y === undefined) continue;
      // 2px surface ring keeps overlapping markers legible without a border.
      el('circle', {
        cx: sx(p.x), cy: sy(p.y), r: 4, fill: color,
        stroke: 'var(--surface-1)', 'stroke-width': 2,
      }, svg);
    }
  });

  // Selective direct labels only -- the endpoint or a named extreme.
  for (const a of annotate) {
    const s = series[a.series ?? 0];
    const p = s.points.find((q) => q.x === a.x);
    if (!p || p.y === null) continue;
    el('text', {
      class: 'mark-label strong', x: sx(p.x), y: sy(p.y) - 11, 'text-anchor': 'middle',
    }, svg).textContent = a.text ?? fmt(p.y, 1);
  }

  // Crosshair band per x position; the hit area is the full column height.
  const crosshair = el('line', {
    class: 'grid-line', y1: padT, y2: padT + plotH, opacity: 0, stroke: 'var(--axis)',
  }, svg);
  const xs = [...new Set(allX)].sort((a, b) => a - b);
  const bandW = plotW / Math.max(1, xs.length - 1 || 1);
  xs.forEach((x) => {
    const hit = el('rect', {
      class: 'hit', x: sx(x) - bandW / 2, y: padT, width: bandW, height: plotH,
    }, svg);
    const rows = series.map((s) => {
      const p = s.points.find((q) => q.x === x);
      return [s.label, p && p.y !== null ? fmt(p.y, 1) : (p?.note || 'bye')];
    });
    hit.addEventListener('mouseenter', (e) => {
      crosshair.setAttribute('x1', sx(x));
      crosshair.setAttribute('x2', sx(x));
      crosshair.setAttribute('opacity', '1');
      showTip(e, `Week ${x}`, rows);
    });
    hit.addEventListener('mousemove', moveTip);
    hit.addEventListener('mouseleave', () => { crosshair.setAttribute('opacity', '0'); hideTip(); });
  });

  const weeks = [...new Set(allX)].sort((a, b) => a - b);
  return figure({
    svg,
    legend: series.length > 1
      ? series.map((s, i) => ({ label: s.label, color: s.color || SERIES(i + 1), line: true }))
      : null,
    table: dataTable(
      [{ label: 'Week' }, ...series.map((s) => ({ label: s.label, num: true }))],
      weeks.map((x) => [
        x,
        ...series.map((s) => {
          const p = s.points.find((q) => q.x === x);
          return p && p.y !== null ? fmt(p.y, 1) : (p?.note || 'bye');
        }),
      ])
    ),
  });
}

// ------------------------------------------------------- range (dot + p10/p90)

/**
 * Draft candidates: a dot for the mean with a thin range behind it. One series,
 * so one color -- the ranking is the y order, not a hue.
 */
export function rangeChart(data, {
  rowH = 30, labelWidth = 210, valueFormat = (v) => fmt(v, 0), xTitle = '', legend = null,
} = {}) {
  const w = 760;
  const padT = 8;
  const h = padT + data.length * rowH + 34;
  const plotLeft = labelWidth;
  const plotW = w - plotLeft - 66;

  const lo = Math.min(...data.map((d) => d.low));
  const hi = Math.max(...data.map((d) => d.high));
  const pad = (hi - lo) * 0.08 || 1;
  const xMin = lo - pad, xMax = hi + pad;
  const sx = (v) => plotLeft + ((v - xMin) / (xMax - xMin)) * plotW;

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMinYMin meet',
    role: 'img', style: `max-height:${h}px`,
  });

  for (let i = 0; i <= 4; i++) {
    const x = plotLeft + (plotW * i) / 4;
    el('line', { class: 'grid-line', x1: x, y1: padT, x2: x, y2: padT + data.length * rowH }, svg);
    el('text', { class: 'tick', x, y: padT + data.length * rowH + 15, 'text-anchor': 'middle' }, svg)
      .textContent = valueFormat(xMin + ((xMax - xMin) * i) / 4);
  }
  if (xTitle) {
    el('text', { class: 'axis-title', x: plotLeft + plotW / 2, y: h - 3, 'text-anchor': 'middle' }, svg)
      .textContent = xTitle;
  }

  data.forEach((d, i) => {
    const y = padT + i * rowH + rowH / 2;
    const color = d.color || SERIES(1);

    el('text', { class: 'mark-label', x: 8, y: y + 4, fill: 'var(--text-primary)' }, svg)
      .textContent = d.label.length > 30 ? `${d.label.slice(0, 29)}…` : d.label;

    el('line', {
      class: 'mark', x1: sx(d.low), y1: y, x2: sx(d.high), y2: y,
      stroke: color, 'stroke-width': 2, opacity: 0.32, 'stroke-linecap': 'round',
    }, svg);
    el('circle', {
      class: 'mark', cx: sx(d.value), cy: y, r: 5.5, fill: color,
      stroke: 'var(--surface-1)', 'stroke-width': 2,
    }, svg);
    el('text', { class: 'mark-label strong', x: sx(d.high) + 9, y: y + 4 }, svg)
      .textContent = valueFormat(d.value);

    const hit = el('rect', { class: 'hit', x: 0, y: y - rowH / 2, width: w, height: rowH }, svg);
    attachTip(hit, d.label, d.rows || [
      ['Expected', valueFormat(d.value)],
      ['Range (p10–p90)', `${valueFormat(d.low)} – ${valueFormat(d.high)}`],
    ]);
  });

  return figure({
    svg,
    legend,
    caption: 'Dot is the expected outcome; the bar spans the 10th to 90th percentile across simulated drafts.',
    table: dataTable(
      [{ label: 'Candidate' }, { label: 'Expected', num: true }, { label: 'p10', num: true }, { label: 'p90', num: true }],
      data.map((d) => [d.label, valueFormat(d.value), valueFormat(d.low), valueFormat(d.high)])
    ),
  });
}

// ------------------------------------------------------------ bye calendar

/**
 * Weeks x count. Sequential single hue, light -> dark; the lightest step means
 * "nobody out" and is allowed to recede toward the surface. Counts are printed
 * in every cell, so the color is reinforcement rather than the only encoding.
 */
export function byeCalendar(weeks, { playoffStart = 15, onClick = null } = {}) {
  const max = Math.max(1, ...weeks.map((w) => w.count));
  const steps = ['var(--seq-100)', 'var(--seq-200)', 'var(--seq-300)', 'var(--seq-450)', 'var(--seq-550)', 'var(--seq-650)'];
  const stepFor = (n) => (n === 0 ? 'var(--surface-3)' : steps[Math.min(steps.length - 1, Math.ceil((n / max) * (steps.length - 1)))]);

  const wrap = html('div', 'bye-grid');
  wrap.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(52px,1fr));gap:6px';

  for (const wk of weeks) {
    const cell = html('div', 'bye-cell');
    const dark = wk.count / max > 0.55;
    cell.style.cssText = `
      border-radius:8px;padding:8px 4px;text-align:center;cursor:${onClick ? 'pointer' : 'default'};
      background:${stepFor(wk.count)};
      color:${wk.count === 0 ? 'var(--text-muted)' : dark ? '#fff' : 'var(--text-primary)'};
      border:1px solid ${wk.week >= playoffStart ? 'var(--series-2)' : 'transparent'};`;
    cell.appendChild(html('div', 'tiny', `W${wk.week}`));
    const v = html('div', null, String(wk.count));
    v.style.cssText = 'font-size:17px;font-weight:650;line-height:1.2';
    cell.appendChild(v);
    if (wk.week >= playoffStart) cell.appendChild(html('div', 'tiny', 'PO'));
    attachTip(cell, `Week ${wk.week}`, [
      ['Players on bye', String(wk.count)],
      ...(wk.names || []).slice(0, 6).map((n) => ['', n]),
    ]);
    if (onClick) cell.addEventListener('click', () => onClick(wk));
    wrap.appendChild(cell);
  }

  return figure({
    svg: wrap,
    caption: 'Darker means more of your players are idle. Orange outline marks the fantasy playoffs.',
    table: dataTable(
      [{ label: 'Week' }, { label: 'Players out', num: true }, { label: 'Who' }],
      weeks.map((w) => [`Week ${w.week}`, w.count, (w.names || []).join(', ') || '—'])
    ),
  });
}

// -------------------------------------------------------------- gauge tile

/** A single probability. One number is a tile, not a chart. */
export function probabilityTile(pct, { label = 'Win probability', sub = '' } = {}) {
  const wrap = html('div');
  const tone = pct >= 60 ? 'var(--good)' : pct >= 40 ? 'var(--warning)' : 'var(--critical)';
  const hero = html('div', 'hero-number', `${fmt(pct, 0)}%`);
  hero.style.color = tone;
  wrap.appendChild(html('div', 'tile-label', label));
  wrap.appendChild(hero);
  if (sub) wrap.appendChild(html('div', 'tile-sub', sub));

  const track = html('div', 'progress-bar');
  track.style.marginTop = '10px';
  const fill = html('div');
  fill.style.width = `${Math.max(2, Math.min(100, pct))}%`;
  fill.style.background = tone;
  track.appendChild(fill);
  wrap.appendChild(track);
  return wrap;
}

export { fmt, dataTable, html as h, el as svgEl };
