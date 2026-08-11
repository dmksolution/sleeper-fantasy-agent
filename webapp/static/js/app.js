// Router, shell and shared context.

import * as api from './api.js';
import { h, clear, toast, callout, loading, empty } from './ui.js';
import { hideTip } from './charts.js';
import { dashboard, draftRoom } from './views-draft.js';
import {
  lineupView, waiversView, matchupView, tradesView,
  playersView, leagueView, digestView, dataView,
} from './views-season.js';

const VIEWS = {
  dashboard: { title: 'Dashboard', icon: '◆', render: dashboard, group: 'Overview' },
  draft: { title: 'Draft Room', icon: '▲', render: draftRoom, group: 'Draft' },
  lineup: { title: 'Lineup', icon: '▤', render: lineupView, group: 'Every week' },
  waivers: { title: 'Waivers', icon: '＋', render: waiversView, group: 'Every week' },
  matchup: { title: 'Matchup', icon: '⚔', render: matchupView, group: 'Every week' },
  trades: { title: 'Trades', icon: '⇄', render: tradesView, group: 'Every week' },
  players: { title: 'Players', icon: '◎', render: playersView, group: 'Research' },
  league: { title: 'League', icon: '☰', render: leagueView, group: 'Research' },
  digest: { title: 'Weekly brief', icon: '▦', render: digestView, group: 'Research' },
  data: { title: 'Data & health', icon: '◇', render: dataView, group: 'System' },
};

const ctx = {
  boot: null,
  week: null,
  params: {},
  viewState: {},
  stream: null,
  draftState: null,
  navigate,
  openPlayer,
  runSync,
};

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  return { name: VIEWS[name] ? name : 'dashboard', params };
}

function navigate(name, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = `#/${name}${qs ? `?${qs}` : ''}`;
}

function openPlayer(playerId) {
  if (!playerId) return;
  ctx.viewState.players ||= { ids: [] };
  const ids = ctx.viewState.players.ids;
  if (!ids.includes(playerId)) ids.push(playerId);
  if (ids.length > 4) ids.shift();
  navigate('players');
}

// ------------------------------------------------------------------- shell

function buildSidebar() {
  const nav = h('nav', { class: 'nav' });
  let lastGroup = null;
  for (const [key, def] of Object.entries(VIEWS)) {
    if (def.group !== lastGroup) {
      nav.appendChild(h('div', { class: 'nav-group-label' }, def.group));
      lastGroup = def.group;
    }
    const badgeEl = h('span', { class: 'nav-badge hidden', id: `badge-${key}` });
    nav.appendChild(h('button', {
      class: 'nav-item', id: `nav-${key}`,
      onclick: () => navigate(key),
    }, h('span', { class: 'nav-icon' }, def.icon), h('span', {}, def.title), badgeEl));
  }
  return nav;
}

function setActiveNav(name) {
  for (const key of Object.keys(VIEWS)) {
    document.getElementById(`nav-${key}`)?.classList.toggle('active', key === name);
  }
}

function themeToggle() {
  const saved = localStorage.getItem('theme');
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = h('button', { class: 'btn ghost sm', title: 'Toggle light and dark' });
  const paint = () => {
    const current = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    btn.textContent = current === 'dark' ? '☾ Dark' : '☀ Light';
  };
  btn.addEventListener('click', () => {
    const current = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
    paint();
  });
  paint();
  return btn;
}

async function runSync(full = false) {
  const t = toast('Syncing', 'Contacting Sleeper…');
  try {
    await api.job('/api/sync', { full }, (msg) => {
      t.querySelector('.t-body').textContent = msg;
    });
    t.querySelector('.t-title').textContent = 'Sync complete';
    t.querySelector('.t-body').textContent = 'Reloading.';
    ctx.boot = await api.get('/api/bootstrap');
    ctx.viewState = {};
    render();
  } catch (err) {
    toast('Sync failed', err.message, 'error');
  }
}

// ------------------------------------------------------------------ render

async function render() {
  const { name, params } = parseHash();
  ctx.params = params;
  setActiveNav(name);

  const def = VIEWS[name];
  document.getElementById('view-title').textContent = def.title;

  const sub = document.getElementById('view-sub');
  sub.textContent = ctx.boot
    ? `${ctx.boot.league.name} · ${ctx.boot.season} · ${ctx.boot.phase === 'season' ? `week ${ctx.boot.week}` : ctx.boot.phase === 'predraft' ? 'pre-draft' : 'drafting'}`
    : '';

  // Close any live stream when leaving the draft room.
  if (name !== 'draft' && ctx.stream) { ctx.stream.close(); ctx.stream = null; }
  hideTip();

  const root = document.getElementById('view');
  // Hold the previous render at reduced opacity rather than flashing a skeleton.
  root.classList.add('refreshing');
  try {
    await def.render(root, ctx);
  } catch (err) {
    clear(root);
    root.appendChild(callout('bad', `<b>${def.title} failed to render.</b><br>${err.message}`));
    console.error(err);
  } finally {
    root.classList.remove('refreshing');
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
}

function updateBadges() {
  const d = ctx.boot?.draft;
  const badge = document.getElementById('badge-draft');
  if (badge && d) {
    if (d.status === 'drafting') {
      badge.textContent = 'LIVE';
      badge.className = 'nav-badge live';
    } else if (d.status === 'pre_draft') {
      badge.textContent = 'soon';
      badge.className = 'nav-badge';
    }
    badge.classList.remove('hidden');
  }
}

// -------------------------------------------------------------------- boot

async function boot() {
  const app = document.getElementById('app');

  const sidebar = h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' },
      h('div', { class: 'brand-name' }, 'Sleeper Fantasy Agent'),
      h('div', { class: 'brand-league', id: 'brand-league' }, 'connecting…'),
      h('div', { class: 'brand-meta', id: 'brand-meta' }, '')),
    buildSidebar(),
    h('div', { class: 'sidebar-footer' },
      h('button', { class: 'btn sm', onclick: () => runSync(false) }, '↻ Sync data'),
      themeToggle()));

  const main = h('main', { class: 'main' },
    h('header', { class: 'topbar' },
      h('h1', { id: 'view-title' }, 'Dashboard'),
      h('span', { class: 'topbar-sub', id: 'view-sub' }, ''),
      h('div', { class: 'topbar-actions', id: 'topbar-actions' })),
    h('div', { class: 'view', id: 'view' }, loading('Loading your league…')));

  app.appendChild(sidebar);
  app.appendChild(main);

  try {
    ctx.boot = await api.get('/api/bootstrap');
  } catch (err) {
    clear(document.getElementById('view'));
    document.getElementById('view').appendChild(
      empty('⚠', 'Could not load your league', err.message,
        h('button', { class: 'btn primary', onclick: () => location.reload() }, 'Retry')));
    return;
  }

  document.getElementById('brand-league').textContent = ctx.boot.league.name;
  document.getElementById('brand-meta').textContent =
    `${ctx.boot.league.teams} teams · ${ctx.boot.league.scoring_format.toUpperCase()} · $${ctx.boot.faab_budget} FAAB`;
  document.title = `${ctx.boot.league.name} — Fantasy Agent`;
  ctx.week = ctx.boot.week;
  updateBadges();

  window.addEventListener('hashchange', render);
  await render();
}

// Keyboard shortcuts: single keys for the views you use under time pressure.
window.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
  const map = { d: 'dashboard', r: 'draft', l: 'lineup', w: 'waivers', m: 'matchup', t: 'trades', p: 'players', g: 'league', b: 'digest', h: 'data' };
  if (map[e.key.toLowerCase()]) { navigate(map[e.key.toLowerCase()]); e.preventDefault(); }
});

boot();
