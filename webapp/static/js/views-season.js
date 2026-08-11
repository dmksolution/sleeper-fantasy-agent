// Lineup, waivers, matchup, trades, players, league and data views.

import * as api from './api.js';
import {
  h, clear, fmt, signed, table, card, tile, empty, callout, loading, toast,
  posChip, slotChip, injuryBadge, badge, unavailableBlock, playerSearch, markdown,
} from './ui.js';
import { barChart, divergingBar, lineChart, byeCalendar, probabilityTile, posColor, positionLegend, SERIES } from './charts.js';

const weekPicker = (ctx, onChange) => h('div', { class: 'field' },
  h('label', {}, 'Week'),
  h('select', { onchange: (e) => onChange(Number(e.target.value)) },
    ...Array.from({ length: 17 }, (_, i) => i + 1).map((w) =>
      h('option', { value: w, selected: (ctx.week || ctx.boot.week) === w },
        `Week ${w}${w >= ctx.boot.playoff_week_start ? ' (playoffs)' : ''}`))));

// ------------------------------------------------------------------ lineup

export async function lineupView(root, ctx) {
  clear(root);
  const week = ctx.week || ctx.boot.week;
  root.appendChild(h('div', { class: 'filter-row' },
    weekPicker(ctx, (w) => { ctx.week = w; lineupView(root, ctx); }),
    h('div', { class: 'spacer' })));

  const holder = h('div', {}, loading('Optimizing your lineup…'));
  root.appendChild(holder);

  try {
    const [lu, ss] = await Promise.all([
      api.get('/api/lineup', { week }),
      api.get('/api/startsit', { week }),
    ]);
    clear(holder);
    if (lu.unavailable) {
      holder.appendChild(unavailableBlock(lu,
        h('button', { class: 'btn primary', onclick: () => ctx.navigate('draft', {}) }, 'Go to the Draft Room')));
      return;
    }

    const left = ss.unavailable ? 0 : (ss.points_left_on_bench || 0);
    const tiles = h('div', { class: 'tiles' });
    tiles.appendChild(tile({ label: 'Optimal projection', value: fmt(lu.projected_total, 1), sub: `week ${week}` }));
    if (!ss.unavailable) {
      tiles.appendChild(tile({
        label: 'Currently set', value: fmt(ss.current_projected, 1),
        sub: left > 0.05 ? 'not optimal' : 'optimal',
      }));
      tiles.appendChild(tile({
        label: 'Points on the bench', value: fmt(left, 1),
        tone: left > 2 ? 'var(--critical)' : left > 0.05 ? 'var(--warning)' : 'var(--good)',
        sub: left > 2 ? 'worth fixing' : left > 0.05 ? 'marginal' : 'nothing to fix',
      }));
    }
    holder.appendChild(tiles);

    if (!ss.unavailable && (ss.start?.length || ss.sit?.length)) {
      const moves = h('div', { class: 'stack' });
      for (const p of ss.start || []) {
        moves.appendChild(h('div', { class: 'row' },
          badge('START', 'good'), slotChip(p.slot), h('b', {}, p.player),
          h('span', { class: 'secondary num' }, fmt(p.points, 1))));
      }
      for (const p of ss.sit || []) {
        moves.appendChild(h('div', { class: 'row' },
          badge('SIT', 'critical'), h('b', {}, p.player),
          h('span', { class: 'secondary num' }, fmt(p.points, 1)),
          p.reason ? h('span', { class: 'small secondary' }, `— ${p.reason}`) : null));
      }
      holder.appendChild(card({ title: `Swaps worth ${fmt(left, 1)} points`, body: moves }));
    }

    holder.appendChild(card({
      title: 'Optimal starters', flush: true,
      body: table([
        { key: 'slot', label: 'Slot', render: (r) => slotChip(r.slot), sortable: false, width: '76px' },
        { key: 'player', label: 'Player', render: (r) => h('span', {}, r.player) },
        { key: 'points', label: 'Proj', num: true, render: (r) => h('b', {}, fmt(r.points, 1)) },
        { key: 'opponent', label: 'Opp', width: '80px' },
        { key: 'note', label: 'Note', cls: 'wrap', render: (r) => r.note ? h('span', { class: 'small secondary' }, r.note) : null },
      ], lu.starters, { onRowClick: (r) => r.player_id && ctx.openPlayer(r.player_id) }),
    }));

    holder.appendChild(card({
      title: 'Bench', flush: true,
      body: table([
        { key: 'player', label: 'Player' },
        { key: 'points', label: 'Proj', num: true, render: (r) => fmt(r.points, 1) },
      ], lu.bench, { initialSort: { key: 'points', dir: 'desc' }, onRowClick: (r) => r.player_id && ctx.openPlayer(r.player_id) }),
    }));
  } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
}

// ----------------------------------------------------------------- waivers

export async function waiversView(root, ctx) {
  clear(root);
  const week = ctx.week || ctx.boot.week;
  root.appendChild(h('div', { class: 'filter-row' },
    weekPicker(ctx, (w) => { ctx.week = w; waiversView(root, ctx); }),
    h('div', { class: 'spacer' }),
    h('button', { class: 'btn primary', onclick: () => run() }, 'Find waiver targets')));

  const out = h('div', {});
  root.appendChild(out);

  const cached = ctx.viewState.waivers;
  if (cached && cached.week === week) draw(cached.data);
  else out.appendChild(empty('▶', 'Rank the wire',
    'Every free agent is scored by how much he would raise your starting lineup, not by talent. ' +
    'Takes a few seconds.'));

  async function run() {
    clear(out);
    const status = loading('Starting…');
    out.appendChild(status);
    try {
      const res = await api.job('/api/waivers', { week, top: 14 },
        (msg, el) => { status.lastChild.textContent = `${msg} (${el}s)`; });
      ctx.viewState.waivers = { week, data: res };
      clear(out);
      draw(res);
    } catch (err) { clear(out); out.appendChild(callout('bad', err.message)); }
  }

  function draw(res) {
    clear(out);
    if (res.unavailable) {
      out.appendChild(unavailableBlock(res,
        h('button', { class: 'btn primary', onclick: () => ctx.navigate('draft', {}) }, 'Go to the Draft Room')));
      return;
    }
    const targets = res.targets || [];
    if (!targets.length) { out.appendChild(empty('—', 'Nothing worth claiming', res.note)); return; }

    const tiles = h('div', { class: 'tiles' });
    if (res.faab_remaining !== undefined && res.faab_remaining !== null) {
      tiles.appendChild(tile({
        label: 'FAAB remaining', value: `$${res.faab_remaining}`,
        sub: `of $${ctx.boot.faab_budget}`,
      }));
    }
    tiles.appendChild(tile({
      label: 'Best lineup gain', value: fmt(targets[0].starting_lineup_gain, 1),
      sub: `${targets[0].player}`, small: false,
    }));
    tiles.appendChild(tile({ label: 'Candidates ranked', value: targets.length }));
    out.appendChild(tiles);

    out.appendChild(card({
      title: 'How much each add raises your starting lineup',
      sub: `week ${res.week}`,
      body: barChart(targets.map((t) => ({
        label: t.player.replace(/\s*\(.*/, ''),
        value: t.starting_lineup_gain,
        color: posColor(t.position),
      })), {
        xTitle: 'Points added to your optimal lineup', barHeight: 20, labelWidth: 156,
        legend: positionLegend(targets),
        tooltipRows: (d) => [['Lineup gain', fmt(d.value, 2)]],
      }),
      note: 'A great player who cannot crack your lineup correctly scores near zero here.',
    }));

    out.appendChild(card({
      title: 'Targets', flush: true,
      body: table([
        { key: 'player', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.player.replace(/\s*\(.*/, '')) },
        { key: 'starting_lineup_gain', label: 'Lineup gain', num: true, render: (r) => h('b', {}, fmt(r.starting_lineup_gain, 2)) },
        { key: 'next_week_points', label: 'Next wk', num: true, render: (r) => fmt(r.next_week_points, 1) },
        { key: 'ros_points', label: 'ROS', num: true, render: (r) => fmt(r.ros_points, 1) },
        { key: 'value_over_replacement', label: 'VOR', num: true, render: (r) => fmt(r.value_over_replacement, 1) },
        { key: 'trending_adds_24h', label: 'Adds 48h', num: true, render: (r) => (r.trending_adds_24h || 0).toLocaleString() },
        { key: 'suggested_faab', label: 'Bid', render: (r) => h('b', {}, r.suggested_faab) },
        { key: 'reason', label: 'Why', cls: 'wrap', sortable: false, render: (r) => h('span', { class: 'small secondary' }, r.reason) },
      ], targets, { initialSort: { key: 'starting_lineup_gain', dir: 'desc' } }),
      note: 'Bids are a share of your remaining budget, scaled by lineup impact, league-wide interest and playoff urgency.',
    }));

    if (res.drops?.length) {
      out.appendChild(card({
        title: 'Drop candidates', sub: 'measured against the wire, not against your starters', flush: true,
        body: table([
          { key: 'player', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.player) },
          { key: 'ros_points', label: 'ROS', num: true, render: (r) => fmt(r.ros_points, 1) },
          { key: 'value_over_replacement', label: 'VOR', num: true, render: (r) => fmt(r.value_over_replacement, 1) },
          { key: 'note', label: 'Note', cls: 'wrap', sortable: false, render: (r) => h('span', { class: 'small secondary' }, r.note) },
        ], res.drops, { initialSort: { key: 'value_over_replacement', dir: 'asc' } }),
      }));
    }
  }
}

// ----------------------------------------------------------------- matchup

export async function matchupView(root, ctx) {
  clear(root);
  const week = ctx.week || ctx.boot.week;
  root.appendChild(h('div', { class: 'filter-row' },
    weekPicker(ctx, (w) => { ctx.week = w; matchupView(root, ctx); }),
    h('div', { class: 'spacer' })));

  const holder = h('div', {}, loading('Reading the matchup…'));
  root.appendChild(holder);

  try {
    const m = await api.get('/api/matchup', { week });
    clear(holder);
    if (m.unavailable) { holder.appendChild(unavailableBlock(m)); return; }

    const grid = h('div', { class: 'grid c2' });
    grid.appendChild(card({
      body: probabilityTile(m.win_probability, {
        label: `Week ${week} win probability`,
        sub: `${fmt(m.my_projected, 1)} projected vs ${fmt(m.opponent_projected, 1)}`,
      }),
      note: 'A normal approximation on the margin with a fixed spread. It separates a coin flip from a real ' +
        'edge, but it does not vary with which players you start, so treat extremes with suspicion.',
    }));
    grid.appendChild(card({
      title: 'Read',
      body: h('div', {},
        h('div', { class: 'tile-label' }, 'Opponent'),
        h('div', { class: 'tile-value sm' }, m.opponent || '—'),
        h('p', { class: 'secondary', style: { marginTop: '10px' } }, m.verdict || ''),
        h('div', { class: 'row', style: { marginTop: '8px' } },
          tile({ label: 'Margin', value: signed(m.my_projected - m.opponent_projected, 1) }))),
    }));
    holder.appendChild(grid);

    const edges = m.positional_edges || [];
    if (edges.length) {
      holder.appendChild(card({
        title: 'Where the matchup is won and lost',
        body: divergingBar(edges.map((e) => ({
          label: e.slot,
          value: e.edge,
          rows: [
            ['You', `${e.mine} (${fmt(e.my_points, 1)})`],
            ['Them', `${e.theirs} (${fmt(e.their_points, 1)})`],
            ['Edge', signed(e.edge, 1)],
          ],
        })), { posLabel: 'You', negLabel: m.opponent || 'Opponent' }),
        note: 'Slot-by-slot projected difference. Blue means you are ahead there.',
      }));
    }
  } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
}

// ------------------------------------------------------------------ trades

export async function tradesView(root, ctx) {
  clear(root);
  const state = ctx.viewState.trade ||= { send: [], receive: [], partner: '' };

  const sendBox = h('div', { class: 'row wrap' });
  const recvBox = h('div', { class: 'row wrap' });

  const renderChips = () => {
    clear(sendBox); clear(recvBox);
    for (const [list, box] of [[state.send, sendBox], [state.receive, recvBox]]) {
      if (!list.length) box.appendChild(h('span', { class: 'small muted' }, 'nobody yet'));
      for (const p of list) {
        box.appendChild(h('span', { class: 'badge solid', style: { cursor: 'pointer' },
          onclick: () => { list.splice(list.indexOf(p), 1); renderChips(); } },
          `${p.name} ✕`));
      }
    }
  };

  const sendSearch = playerSearch({ placeholder: 'Player you send…', onPick: (p) => { state.send.push(p); renderChips(); } });
  const recvSearch = playerSearch({ placeholder: 'Player you receive…', onPick: (p) => { state.receive.push(p); renderChips(); } });

  const partnerSel = h('select', { onchange: (e) => { state.partner = e.target.value; } },
    h('option', { value: '' }, 'Partner (optional)'));

  api.get('/api/standings').then((s) => {
    for (const row of s.standings || []) {
      partnerSel.appendChild(h('option', { value: row.roster_id, selected: String(state.partner) === String(row.roster_id) }, row.team));
    }
  }).catch(() => {});

  const out = h('div', {});

  root.appendChild(card({
    title: 'Evaluate a trade',
    sub: 're-optimizes both rosters, week by week',
    body: h('div', {},
      h('div', { class: 'grid c2' },
        h('div', { class: 'field' }, h('label', {}, 'You send'), sendSearch.el, sendBox),
        h('div', { class: 'field' }, h('label', {}, 'You receive'), recvSearch.el, recvBox)),
      h('div', { class: 'row', style: { marginTop: '14px' } },
        partnerSel,
        h('button', {
          class: 'btn primary',
          onclick: async () => {
            if (!state.send.length || !state.receive.length) { toast('Add players first', 'Both sides need at least one player.', 'error'); return; }
            clear(out); out.appendChild(loading('Re-optimizing both rosters…'));
            try {
              const res = await api.post('/api/trade/evaluate', {
                send: state.send.map((p) => p.name),
                receive: state.receive.map((p) => p.name),
                partner: state.partner ? Number(state.partner) : null,
                week: ctx.week || ctx.boot.week,
              });
              clear(out);
              drawTrade(res, out, ctx);
            } catch (err) { clear(out); out.appendChild(callout('bad', err.message)); }
          },
        }, 'Evaluate'),
        h('button', { class: 'btn ghost', onclick: () => { state.send = []; state.receive = []; renderChips(); clear(out); } }, 'Clear'))),
  }));
  renderChips();
  root.appendChild(out);

  // Trade targets
  const targetsCard = card({ title: 'Who to trade with', sub: 'their bench surplus against your weakest slots', body: loading() });
  root.appendChild(targetsCard);
  api.get('/api/trade/targets', { week: ctx.week || ctx.boot.week }).then((res) => {
    const body = targetsCard.querySelector('.card-body');
    clear(body);
    if (res.unavailable) return body.appendChild(unavailableBlock(res));
    const rows = res.targets || res.opportunities || [];
    if (!rows.length) return body.appendChild(empty('—', 'No obvious fits', 'Check back once rosters settle.'));
    body.appendChild(h('pre', { class: 'mono small', style: { whiteSpace: 'pre-wrap', margin: 0 } },
      JSON.stringify(rows, null, 2)));
  }).catch((e) => { const b = targetsCard.querySelector('.card-body'); clear(b); b.appendChild(callout('bad', e.message)); });
}

function drawTrade(res, out, ctx) {
  if (res.unavailable) { out.appendChild(unavailableBlock(res)); return; }
  const net = res.net_starting_points;
  const tone = net > 1 ? 'ok' : net < -1 ? 'bad' : 'warn';
  out.appendChild(callout(tone, `<b>${res.verdict}</b> — ${signed(net, 1)} starting points over ${res.weeks_evaluated} weeks.`, '★'));

  if (res.coverage_warning) out.appendChild(callout('warn', res.coverage_warning));

  const tiles = h('div', { class: 'tiles' });
  tiles.appendChild(tile({ label: 'Net starting points', value: signed(net, 1), tone: net > 0 ? 'var(--good)' : net < 0 ? 'var(--critical)' : null }));
  tiles.appendChild(tile({ label: 'Raw ROS swing', value: signed(res.raw_points_swing, 1) }));
  tiles.appendChild(tile({ label: 'Roster slots', value: signed(res.roster_slots_change, 0), sub: 'you must drop if positive' }));
  if (res.partner) {
    tiles.appendChild(tile({
      label: 'For them', value: signed(res.partner.net_starting_points, 1),
      sub: res.likely_accepted ? 'they gain — plausible' : 'they lose — unlikely',
      tone: res.likely_accepted ? 'var(--good)' : 'var(--warning)',
    }));
  }
  out.appendChild(tiles);

  out.appendChild(card({
    title: 'Both sides',
    body: divergingBar([
      { label: 'You', value: net, rows: [['Net starting points', signed(net, 1)]] },
      ...(res.partner ? [{ label: res.partner.team, value: res.partner.net_starting_points,
        rows: [['Net starting points', signed(res.partner.net_starting_points, 1)]] }] : []),
    ], { posLabel: 'Gains', negLabel: 'Loses' }),
    note: 'A trade both sides gain from is the only kind that gets accepted.',
  }));

  const rows = [
    ...res.you_send.map((p) => ({ side: 'Send', ...p })),
    ...res.you_receive.map((p) => ({ side: 'Receive', ...p })),
  ];
  out.appendChild(card({
    title: 'Players', flush: true,
    body: table([
      { key: 'side', label: 'Side', render: (r) => badge(r.side, r.side === 'Receive' ? 'good' : 'critical'), width: '92px' },
      { key: 'player', label: 'Player' },
      { key: 'ros_points', label: 'ROS points', num: true, render: (r) => fmt(r.ros_points, 1) },
    ], rows, { }),
  }));
}

// ----------------------------------------------------------------- players

export async function playersView(root, ctx) {
  clear(root);
  const state = ctx.viewState.players ||= { ids: [] };

  const search = playerSearch({
    placeholder: 'Search any NFL player…',
    onPick: (p) => { if (!state.ids.includes(p.player_id)) state.ids.push(p.player_id); if (state.ids.length > 4) state.ids.shift(); render(); },
  });
  root.appendChild(h('div', { class: 'filter-row' },
    h('div', { class: 'field' }, h('label', {}, 'Add a player (up to 4 to compare)'), search.el),
    h('div', { class: 'spacer' }),
    h('button', { class: 'btn ghost', onclick: () => { state.ids = []; render(); } }, 'Clear')));

  const out = h('div', {});
  root.appendChild(out);

  async function render() {
    clear(out);
    if (!state.ids.length) {
      out.appendChild(empty('🔎', 'Look up a player',
        'Weekly projections, byes, playoff-week value and rest-of-season totals. Add up to four to compare.'));
      return;
    }
    out.appendChild(loading('Loading players…'));
    try {
      const res = await api.get('/api/compare', { ids: state.ids.join(','), week: ctx.week || ctx.boot.week });
      clear(out);
      const players = (res.players || []).filter((p) => !p.unavailable);
      if (!players.length) { out.appendChild(empty('—', 'Not found')); return; }

      const tiles = h('div', { class: 'tiles' });
      for (const p of players) {
        tiles.appendChild(tile({
          label: `${p.position} · ${p.team || 'FA'}${p.free_agent ? ' · FREE AGENT' : ''}`,
          value: fmt(p.season_points, 0),
          sub: `${p.games} games · ${fmt(p.ppg, 1)}/gm · bye ${p.bye_weeks?.[0] ?? '—'}`,
          small: false,
        }));
      }
      out.appendChild(tiles);

      out.appendChild(card({
        title: 'Weekly projection',
        body: lineChart(players.map((p, i) => ({
          label: p.name,
          color: SERIES(i + 1),
          points: p.weekly.map((w) => ({ x: w.week, y: w.points, note: w.bye ? 'bye' : null })),
        })), {
          height: 260,
          yTitle: 'Projected points',
          markers: [{ from: ctx.boot.playoff_week_start, to: 17, label: 'fantasy playoffs' }],
          xLabels: Array.from({ length: 17 }, (_, i) => i + 1).filter((w) => w % 2 === 1).map((w) => ({ x: w, label: String(w) })),
          annotate: players.length === 1 ? [{ series: 0, x: 1 }] : [],
        }),
        note: 'Gaps are bye weeks. The shaded band is the fantasy playoffs.',
      }));

      out.appendChild(card({
        title: 'Detail', flush: true,
        body: table([
          { key: 'name', label: 'Player', render: (p) => h('span', {}, posChip(p.position), ' ', p.name, ' ', injuryBadge(p.injury_status)) },
          { key: 'team', label: 'Team', width: '70px' },
          { key: 'adp', label: 'ADP', num: true, render: (p) => fmt(p.adp, 1) },
          { key: 'season_points', label: 'Season', num: true, render: (p) => h('b', {}, fmt(p.season_points, 0)) },
          { key: 'ppg', label: 'Per game', num: true, render: (p) => fmt(p.ppg, 1) },
          { key: 'playoff_points', label: 'Wk15-17', num: true, render: (p) => fmt(p.playoff_points, 0) },
          { key: 'rest_of_season', label: 'ROS', num: true, render: (p) => fmt(p.rest_of_season, 1) },
          { key: 'bye', label: 'Bye', num: true, render: (p) => p.bye_weeks?.[0] ?? '—' },
          { key: 'rostered_by', label: 'Rostered by', render: (p) => p.rostered_by || h('span', { class: 'up' }, 'free agent') },
        ], players, {}),
      }));
    } catch (err) { clear(out); out.appendChild(callout('bad', err.message)); }
  }

  render();
  ctx.renderPlayers = render;
  ctx.addPlayer = (id) => { if (!state.ids.includes(id)) state.ids.push(id); if (state.ids.length > 4) state.ids.shift(); render(); };
}

// ------------------------------------------------------------------ league

export async function leagueView(root, ctx) {
  clear(root);
  const holder = h('div', {}, loading('Loading the league…'));
  root.appendChild(holder);

  try {
    const [st, by, act, tr] = await Promise.all([
      api.get('/api/standings'),
      api.get('/api/byes'),
      api.get('/api/activity').catch(() => ({ activity: [] })),
      api.get('/api/trending', { kind: 'add' }).catch(() => ({ players: [] })),
    ]);
    clear(holder);

    const rows = st.standings || [];
    if (rows.length) {
      holder.appendChild(card({
        title: 'Standings', flush: true,
        body: table([
          { key: 'team', label: 'Team' },
          { key: 'wins', label: 'W', num: true, width: '48px' },
          { key: 'losses', label: 'L', num: true, width: '48px' },
          { key: 'ties', label: 'T', num: true, width: '44px' },
          { key: 'points_for', label: 'PF', num: true, render: (r) => fmt(r.points_for, 1) },
          { key: 'points_against', label: 'PA', num: true, render: (r) => fmt(r.points_against, 1) },
          { key: 'waiver_budget_used', label: 'FAAB used', num: true, render: (r) => `$${r.waiver_budget_used ?? 0}` },
        ], rows, { initialSort: { key: 'wins', dir: 'desc' } }),
      }));

      holder.appendChild(card({
        title: 'Points for',
        body: barChart(rows.map((r) => ({ label: r.team, value: r.points_for })), {
          xTitle: 'Points scored', barHeight: 20, labelWidth: 170,
        }),
      }));
    }

    // Bye calendar from the roster, if there is one.
    const byWeek = by.by_week || {};
    const weeks = Array.from({ length: 17 }, (_, i) => i + 1).map((w) => ({
      week: w,
      count: (byWeek[String(w)] || []).length,
      names: (byWeek[String(w)] || []).map((p) => p.player),
    }));
    if (weeks.some((w) => w.count)) {
      holder.appendChild(card({
        title: 'Your bye weeks',
        body: byeCalendar(weeks, { playoffStart: ctx.boot.playoff_week_start }),
      }));
    } else {
      holder.appendChild(card({
        title: 'Bye weeks',
        body: empty('○', 'No roster yet', 'This fills in once you have drafted.'),
      }));
    }

    if (tr.players?.length) {
      holder.appendChild(card({
        title: 'Trending adds league-wide', sub: 'last 48 hours', flush: true,
        body: table([
          { key: 'label', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.label) },
          { key: 'count', label: 'Adds', num: true, render: (r) => r.count.toLocaleString() },
          { key: 'rostered', label: 'Status', render: (r) => r.rostered ? badge('rostered') : badge('free agent', 'good') },
        ], tr.players, { initialSort: { key: 'count', dir: 'desc' }, maxHeight: '380px',
          onRowClick: (r) => ctx.openPlayer(r.player_id) }),
      }));
    }

    if (act.activity?.length) {
      holder.appendChild(card({
        title: 'Recent roster changes',
        body: h('div', { class: 'stack' }, act.activity.map((a) =>
          h('div', { class: 'small' }, JSON.stringify(a)))),
      }));
    }
  } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
}

// ------------------------------------------------------------------ digest

export async function digestView(root, ctx) {
  clear(root);
  const week = ctx.week || ctx.boot.week;
  root.appendChild(h('div', { class: 'filter-row' },
    weekPicker(ctx, (w) => { ctx.week = w; digestView(root, ctx); }),
    h('div', { class: 'spacer' }),
    h('button', { class: 'btn', onclick: send }, 'Push to my phone'),
    h('button', { class: 'btn primary', onclick: build }, 'Build the brief')));

  const out = h('div', {});
  root.appendChild(out);

  const cached = ctx.viewState.digest;
  if (cached?.week === week) show(cached.markdown);
  else out.appendChild(empty('▤', 'The weekly brief',
    'Matchup, optimal lineup, waiver targets with bids, league activity, trade angles, byes and standings — ' +
    'in one report. Syncs first, so it takes a moment.'));

  async function build() {
    clear(out);
    const status = loading('Starting…');
    out.appendChild(status);
    try {
      const res = await api.job('/api/digest', { week },
        (msg, el) => { status.lastChild.textContent = `${msg} (${el}s)`; });
      ctx.viewState.digest = { week, markdown: res.markdown };
      clear(out); show(res.markdown);
    } catch (err) { clear(out); out.appendChild(callout('bad', err.message)); }
  }

  async function send() {
    try {
      const res = await api.post('/api/digest/notify', { week });
      const sent = res.sent || {};
      if (sent.ntfy === 200 || sent.webhook === 200) toast('Sent', 'Check your phone.', 'success');
      else toast('Not sent', sent.note || JSON.stringify(sent), 'error');
    } catch (err) { toast('Failed', err.message, 'error'); }
  }

  function show(md) {
    out.appendChild(card({ body: h('div', { class: 'markdown', html: markdown(md) }) }));
  }
}

// -------------------------------------------------------------------- data

export async function dataView(root, ctx) {
  clear(root);
  const holder = h('div', {}, loading('Checking the data…'));
  root.appendChild(holder);

  try {
    const hp = await api.get('/api/health');
    clear(holder);

    const ok = hp.coverage_complete;
    holder.appendChild(callout(ok ? 'ok' : 'warn',
      ok ? 'All 18 weeks of projections are cached and scoring reconciles at every position.'
         : `Missing weeks ${(hp.projection_weeks_missing || []).join(', ')} — run a sync.`));

    const tiles = h('div', { class: 'tiles' });
    tiles.appendChild(tile({ label: 'Weeks cached', value: `${(hp.projection_weeks_cached || []).length}/18`, tone: ok ? 'var(--good)' : 'var(--warning)' }));
    tiles.appendChild(tile({ label: 'Byes detected', value: `${hp.nfl_teams_with_bye_detected}/32` }));
    for (const [k, v] of Object.entries(hp.row_counts || {})) {
      tiles.appendChild(tile({ label: k, value: Number(v).toLocaleString(), small: true }));
    }
    holder.appendChild(tiles);

    const audit = Object.entries(hp.scoring_audit || {}).map(([pos, a]) => ({ pos, ...a }));
    holder.appendChild(card({
      title: 'Scoring audit',
      sub: 'our dot product against Sleeper\'s own number',
      flush: true,
      body: table([
        { key: 'pos', label: 'Position', render: (r) => posChip(r.pos), width: '90px' },
        { key: 'worst_abs_diff_vs_sleeper', label: 'Worst difference', num: true, render: (r) => fmt(r.worst_abs_diff_vs_sleeper, 2) },
        { key: 'ok', label: 'Verdict', render: (r) => r.ok ? badge('within tolerance', 'good') : badge('investigate', 'critical') },
        { key: 'note', label: 'Note', cls: 'wrap', sortable: false, render: (r) => h('span', { class: 'small secondary' }, r.note || '') },
      ], audit, {}),
      note: 'QB is expected to differ: Sleeper\'s pts_ppr scores interceptions at +2 while this league scores ' +
        'them at -1, so their number runs 3 x pass_int high. Our number is the correct one.',
    }));

    if (hp.warnings?.length) {
      holder.appendChild(card({
        title: 'Warnings',
        body: h('ul', { style: { margin: 0, paddingLeft: '18px' } },
          hp.warnings.map((w) => h('li', { class: 'small' }, w))),
      }));
    }

    holder.appendChild(card({
      title: 'League configuration',
      flush: true,
      body: table([
        { key: 'k', label: 'Setting', sortable: false },
        { key: 'v', label: 'Value', sortable: false },
      ], Object.entries(ctx.boot.league).map(([k, v]) => ({
        k, v: Array.isArray(v) ? v.join(', ') : String(v),
      })), {}),
    }));
  } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
}
