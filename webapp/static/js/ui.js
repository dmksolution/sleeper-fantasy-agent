// Shared DOM helpers and small components.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(3)) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

export const fmt = (v, d = 1) =>
  v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d);

export const signed = (v, d = 1) =>
  v === null || v === undefined ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}`;

export const pct = (v, d = 0) => (v === null || v === undefined ? '—' : `${(Number(v) * 100).toFixed(d)}%`);

export const posChip = (pos) => h('span', { class: `pos pos-${pos || 'FLEX'}` }, pos || '—');

export const slotChip = (slot) => h('span', { class: 'slot-chip' }, slot);

export function badge(text, tone) {
  return h('span', { class: `badge${tone ? ` ${tone}` : ''}` }, text);
}

/** Injury tags never rely on color alone -- the tag text is always present. */
export function injuryBadge(status) {
  if (!status) return null;
  const s = String(status).toUpperCase();
  const tone = ['OUT', 'IR', 'PUP', 'SUS', 'DNR', 'NA'].includes(s)
    ? 'critical' : s === 'DOUBTFUL' ? 'serious' : 'warning';
  return badge(s.slice(0, 12), tone);
}

export function tile({ label, value, sub, delta, deltaDir, tone, small }) {
  return h('div', { class: 'tile' },
    h('div', { class: 'tile-label' }, label),
    h('div', { class: `tile-value${small ? ' sm' : ''}`, style: tone ? { color: tone } : {} }, value),
    sub ? h('div', { class: 'tile-sub' }, sub) : null,
    delta ? h('div', { class: `tile-delta ${deltaDir || ''}` }, delta) : null,
  );
}

export function card({ title, sub, actions, body, note, flush }) {
  const head = (title || actions)
    ? h('div', { class: 'card-head' },
        title ? h('h2', {}, title) : null,
        sub ? h('span', { class: 'sub' }, sub) : null,
        actions ? h('div', { class: 'actions' }, actions) : null)
    : null;
  return h('div', { class: 'card' },
    head,
    h('div', { class: `card-body${flush ? ' flush' : ''}` }, body),
    note ? h('div', { class: 'card-note' }, note) : null);
}

export function empty(icon, title, message, action) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-icon' }, icon),
    h('h3', {}, title),
    message ? h('p', {}, message) : null,
    action || null);
}

export function callout(tone, text, icon) {
  return h('div', { class: `callout ${tone}` },
    h('span', { class: 'ico' }, icon ?? (tone === 'bad' ? '!' : tone === 'warn' ? '!' : 'i')),
    h('div', { html: text }));
}

export function loading(message = 'Working...') {
  return h('div', { class: 'loading-wrap' }, h('div', { class: 'spinner' }), h('span', {}, message));
}

/** Renders an `unavailable` payload from the API as a useful dead end. */
export function unavailableBlock(payload, extraAction) {
  return empty(
    payload.empty_roster ? '○' : '⚠',
    payload.reason,
    payload.action,
    extraAction);
}

/**
 * Sortable table. Column defs: {key,label,num,render,sortValue,width,cls}
 */
export function table(columns, rows, { onRowClick = null, initialSort = null, maxHeight = null, tall = false } = {}) {
  let sortKey = initialSort?.key ?? null;
  let sortDir = initialSort?.dir ?? 'desc';

  const scroll = h('div', { class: `table-scroll${tall ? ' tall' : ''}` });
  if (maxHeight) scroll.style.maxHeight = maxHeight;
  const tbl = h('table', { class: 'data' });
  const thead = h('thead');
  const tbody = h('tbody');

  const headRow = h('tr');
  for (const col of columns) {
    const th = h('th', {
      class: `${col.num ? 'num' : ''}${col.sortable === false ? '' : ' sortable'}`.trim(),
      style: col.width ? { width: col.width } : {},
      onclick: col.sortable === false ? null : () => {
        if (sortKey === col.key) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
        else { sortKey = col.key; sortDir = col.num ? 'desc' : 'asc'; }
        draw();
      },
    }, col.label, sortKey === col.key ? h('span', { class: 'muted' }, sortDir === 'desc' ? ' ↓' : ' ↑') : null);
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);

  function draw() {
    clear(tbody);
    let data = [...rows];
    if (sortKey) {
      const col = columns.find((c) => c.key === sortKey);
      const val = (r) => (col.sortValue ? col.sortValue(r) : r[sortKey]);
      data.sort((a, b) => {
        const x = val(a), y = val(b);
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        const cmp = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
        return sortDir === 'desc' ? -cmp : cmp;
      });
    }
    for (const row of data) {
      const tr = h('tr', { class: onRowClick ? 'clickable' : '' });
      if (onRowClick) tr.addEventListener('click', () => onRowClick(row));
      for (const col of columns) {
        const content = col.render ? col.render(row) : row[col.key];
        const td = h('td', { class: `${col.num ? 'num' : ''} ${col.cls || ''}`.trim() });
        if (content !== null && content !== undefined) {
          td.appendChild(typeof content === 'object' ? content : document.createTextNode(String(content)));
        } else td.textContent = '—';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    // Re-render headers so the sort arrow follows the active column.
    clear(headRow);
    for (const col of columns) {
      const th = h('th', {
        class: `${col.num ? 'num' : ''}${col.sortable === false ? '' : ' sortable'}`.trim(),
        style: col.width ? { width: col.width } : {},
        onclick: col.sortable === false ? null : () => {
          if (sortKey === col.key) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
          else { sortKey = col.key; sortDir = col.num ? 'desc' : 'asc'; }
          draw();
        },
      }, col.label, sortKey === col.key ? h('span', { class: 'muted' }, sortDir === 'desc' ? ' ↓' : ' ↑') : null);
      headRow.appendChild(th);
    }
  }

  draw();
  tbl.appendChild(thead);
  tbl.appendChild(tbody);
  scroll.appendChild(tbl);
  return scroll;
}

// ------------------------------------------------------------------ toasts

let toastHost = null;
export function toast(title, body, tone = '') {
  if (!toastHost) {
    toastHost = h('div', { class: 'toasts' });
    document.body.appendChild(toastHost);
  }
  const node = h('div', { class: `toast ${tone}` },
    h('div', { class: 't-title' }, title),
    body ? h('div', { class: 't-body' }, body) : null);
  toastHost.appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .3s';
    setTimeout(() => node.remove(), 320);
  }, tone === 'error' ? 8000 : 4200);
  return node;
}

// ------------------------------------------------------------- player search

/** Typeahead that resolves to a player object. Used by trades and compare. */
export function playerSearch({ placeholder = 'Search players…', onPick, value = '' }) {
  const input = h('input', { type: 'search', placeholder, value, autocomplete: 'off' });
  const menu = h('div', {
    class: 'card',
    style: {
      position: 'absolute', zIndex: '60', marginTop: '2px', maxHeight: '260px',
      overflow: 'auto', width: '280px', display: 'none', padding: '4px',
    },
  });
  const wrap = h('div', { style: { position: 'relative' } }, input, menu);

  let timer = null;
  const close = () => { menu.style.display = 'none'; };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/players/search?q=${encodeURIComponent(q)}`).then((r) => r.json());
        clear(menu);
        if (!res.players?.length) return close();
        for (const p of res.players) {
          const row = h('button', {
            class: 'nav-item',
            onclick: () => { onPick(p); input.value = ''; close(); },
          }, posChip(p.position), h('span', {}, `${p.name}`), h('span', { class: 'nav-badge' }, p.team || 'FA'));
          menu.appendChild(row);
        }
        menu.style.display = 'block';
      } catch { close(); }
    }, 180);
  });

  input.addEventListener('blur', () => setTimeout(close, 180));
  return { el: wrap, input };
}

/** Minimal markdown -> HTML for the digest view. */
export function markdown(src) {
  const lines = String(src).split('\n');
  let out = '';
  let inTable = false;
  let inList = false;

  const inline = (s) => s
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\*(.+?)\*/g, '<i>$1</i>');

  const closeList = () => { if (inList) { out += '</ul>'; inList = false; } };
  const closeTable = () => { if (inTable) { out += '</tbody></table>'; inTable = false; } };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\|[\s-:|]+\|$/.test(line)) continue;               // table divider
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1).map((c) => inline(c.trim()));
      if (!inTable) {
        closeList();
        out += `<table><thead><tr>${cells.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>`;
        inTable = true;
      } else {
        out += `<tr>${cells.map((c) => `<td>${c}</td>`).join('')}</tr>`;
      }
      continue;
    }
    closeTable();
    if (/^#{1,6}\s/.test(line)) {
      closeList();
      const level = line.match(/^#+/)[0].length;
      out += `<h${Math.min(level + 1, 4)}>${inline(line.replace(/^#+\s*/, ''))}</h${Math.min(level + 1, 4)}>`;
    } else if (/^[-*]\s/.test(line)) {
      if (!inList) { out += '<ul>'; inList = true; }
      out += `<li>${inline(line.replace(/^[-*]\s*/, ''))}</li>`;
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  closeTable();
  return out;
}
