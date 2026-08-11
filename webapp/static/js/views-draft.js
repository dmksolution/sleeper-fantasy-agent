// Dashboard and the Draft Room.

import * as api from './api.js';
import { h, clear, fmt, signed, table, card, tile, empty, callout, loading, toast, posChip, injuryBadge, unavailableBlock } from './ui.js';
import { barChart, rangeChart, divergingBar, probabilityTile, posColor, positionLegend, SERIES } from './charts.js';

// ---------------------------------------------------------------- dashboard

export async function dashboard(root, ctx) {
  const boot = ctx.boot;
  clear(root);

  const warnings = boot.health.warnings || [];
  if (!boot.health.coverage_complete) {
    root.appendChild(callout('warn',
      `Only <b>${boot.health.weeks_cached}</b> weeks of projections are cached, so rest-of-season and ` +
      `bye numbers are incomplete. Run a sync to fix it.`));
  }

  const tiles = h('div', { class: 'tiles' });
  tiles.appendChild(tile({
    label: 'Phase',
    value: boot.phase === 'predraft' ? 'Pre-draft' : boot.phase === 'drafting' ? 'Drafting' : `Week ${boot.week}`,
    sub: boot.league.name,
  }));
  tiles.appendChild(tile({
    label: 'Roster', value: boot.roster_size, sub: `${boot.starting_slots.length} starters, ${boot.bench_size} bench`,
  }));
  tiles.appendChild(tile({
    label: 'FAAB budget', value: `$${boot.faab_budget}`, sub: 'Waivers process Tuesday',
  }));
  tiles.appendChild(tile({
    label: 'Data', value: `${boot.health.weeks_cached}/18`, small: true,
    sub: boot.health.coverage_complete ? 'All weeks cached' : 'Incomplete — sync',
    tone: boot.health.coverage_complete ? 'var(--good)' : 'var(--warning)',
  }));
  root.appendChild(tiles);

  if (boot.phase === 'predraft' || boot.phase === 'drafting') {
    root.appendChild(await predraftDashboard(ctx));
  } else {
    root.appendChild(await seasonDashboard(ctx));
  }

  if (warnings.length) {
    root.appendChild(card({
      title: 'Data notes',
      body: h('ul', { style: { margin: 0, paddingLeft: '18px' } },
        warnings.map((w) => h('li', { class: 'small secondary' }, w))),
    }));
  }
}

async function predraftDashboard(ctx) {
  const wrap = h('div');
  const d = ctx.boot.draft;

  const slotLine = !d?.my_slot
    ? callout('warn',
        'Your draft slot is not published yet. That is normal — commissioners usually set the order ' +
        'at the last minute. Study <b>every</b> slot in the Draft Room, and set your slot there once you know it.')
    : callout('ok', `You are picking from <b>slot ${d.my_slot}</b> (${d.slot_source}).`);
  wrap.appendChild(slotLine);

  const grid = h('div', { class: 'grid side' });

  const dissentCard = card({
    title: 'Biggest model vs market gaps',
    sub: 'review these by hand',
    actions: h('button', { class: 'btn sm', onclick: () => ctx.navigate('draft', { tab: 'dissent' }) }, 'Open'),
    body: loading('Comparing projections against ADP…'),
  });
  grid.appendChild(dissentCard);

  const checklist = card({
    title: 'Draft prep checklist',
    body: h('div', { class: 'stack' },
      checkItem('Cache all 18 weeks of projections', ctx.boot.health.coverage_complete,
        () => ctx.runSync()),
      checkItem('Review model vs market disagreements', false,
        () => ctx.navigate('draft', { tab: 'dissent' })),
      checkItem('Study the outcome from each draft slot', false,
        () => ctx.navigate('draft', { tab: 'plan' })),
      checkItem('Warm the board before draft night', false,
        () => ctx.navigate('draft', { tab: 'recommend' })),
      checkItem('Know your slot', Boolean(d?.my_slot),
        () => ctx.navigate('draft', {}))),
  });
  grid.appendChild(checklist);
  wrap.appendChild(grid);

  api.get('/api/draft/dissent', { top: 6 }).then((res) => {
    const body = dissentCard.querySelector('.card-body');
    clear(body);
    const rows = (res.we_like_more_than_market || []).slice(0, 6);
    if (!rows.length) return body.appendChild(empty('—', 'No ADP data yet', 'Run a sync first.'));
    body.appendChild(barChart(
      rows.map((r) => ({ label: r.player.replace(/\s*\(.*/, ''), value: r.edge, color: posColor(r.position) })),
      { xTitle: 'Picks later than our value says', barHeight: 20, labelWidth: 150,
        valueFormat: (v) => fmt(v, 0), legend: positionLegend(rows),
        tooltipRows: (d0) => [['Edge', `${fmt(d0.value, 0)} picks`]] }));
    body.appendChild(h('div', { class: 'tiny muted' },
      'Positive edge = the market lets him fall further than our projection says he should.'));
  }).catch((e) => {
    const body = dissentCard.querySelector('.card-body');
    clear(body); body.appendChild(callout('bad', e.message));
  });

  return wrap;
}

function checkItem(label, done, onClick) {
  return h('button', {
    class: 'nav-item', onclick: onClick,
    style: { border: '1px solid var(--border)', marginBottom: '4px' },
  },
    h('span', { class: 'nav-icon', style: { color: done ? 'var(--good)' : 'var(--text-muted)' } }, done ? '✓' : '○'),
    h('span', {}, label));
}

async function seasonDashboard(ctx) {
  const wrap = h('div');
  const grid = h('div', { class: 'grid side' });

  const matchCard = card({ title: `Week ${ctx.boot.week} matchup`, body: loading() });
  const lineupCard = card({ title: 'Lineup check', body: loading() });
  grid.appendChild(matchCard);
  grid.appendChild(lineupCard);
  wrap.appendChild(grid);

  api.get('/api/matchup', { week: ctx.boot.week }).then((m) => {
    const body = matchCard.querySelector('.card-body');
    clear(body);
    if (m.unavailable) return body.appendChild(unavailableBlock(m));
    body.appendChild(renderMatchupSummary(m));
  }).catch((e) => { const b = matchCard.querySelector('.card-body'); clear(b); b.appendChild(callout('bad', e.message)); });

  api.get('/api/startsit', { week: ctx.boot.week }).then((s) => {
    const body = lineupCard.querySelector('.card-body');
    clear(body);
    if (s.unavailable) return body.appendChild(unavailableBlock(s));
    const left = s.points_left_on_bench || 0;
    body.appendChild(tile({
      label: 'Points on your bench', value: fmt(left, 1),
      tone: left > 2 ? 'var(--critical)' : 'var(--good)',
      sub: left > 2 ? 'You have a swap worth making' : 'Your lineup is optimal',
    }));
    if (s.start?.length) {
      body.appendChild(h('div', { class: 'small strong', style: { marginTop: '12px' } }, 'Start'));
      for (const p of s.start) body.appendChild(h('div', { class: 'small' }, `${p.player} → ${p.slot} (${fmt(p.points, 1)})`));
    }
    if (s.sit?.length) {
      body.appendChild(h('div', { class: 'small strong', style: { marginTop: '8px' } }, 'Sit'));
      for (const p of s.sit) body.appendChild(h('div', { class: 'small secondary' }, `${p.player} — ${p.reason || 'lower projection'}`));
    }
  }).catch((e) => { const b = lineupCard.querySelector('.card-body'); clear(b); b.appendChild(callout('bad', e.message)); });

  return wrap;
}

export function renderMatchupSummary(m) {
  const wrap = h('div');
  const row = h('div', { class: 'grid c2' });
  row.appendChild(probabilityTile(m.win_probability, {
    label: 'Win probability',
    sub: `${fmt(m.my_projected, 1)} vs ${fmt(m.opponent_projected, 1)} projected`,
  }));
  row.appendChild(h('div', {},
    h('div', { class: 'tile-label' }, 'Opponent'),
    h('div', { class: 'tile-value sm' }, m.opponent || '—'),
    h('div', { class: 'tile-sub' }, m.verdict || '')));
  wrap.appendChild(row);
  return wrap;
}

// --------------------------------------------------------------- draft room

export async function draftRoom(root, ctx) {
  clear(root);
  const state = ctx.viewState.draft ||= { tab: 'recommend', slot: null, live: false, sim: null, plan: null };
  if (ctx.params.tab) state.tab = ctx.params.tab;
  // Deep links can pin the slot, which is how you share "here is my draft view".
  if (ctx.params.slot) state.slot = Number(ctx.params.slot) || null;

  const header = h('div', { id: 'draft-header' });
  root.appendChild(header);

  const tabs = h('div', { class: 'seg', style: { marginBottom: '16px' } });
  const tabDefs = [
    ['recommend', 'Recommend'],
    ['board', 'Value board'],
    ['dissent', 'Model vs market'],
    ['plan', 'Slot planner'],
    ['recap', 'Recap'],
  ];
  for (const [key, label] of tabDefs) {
    tabs.appendChild(h('button', {
      class: state.tab === key ? 'active' : '',
      onclick: () => { state.tab = key; ctx.navigate('draft', { tab: key }); },
    }, label));
  }
  root.appendChild(tabs);

  const panel = h('div', { id: 'draft-panel' });
  root.appendChild(panel);

  async function refreshHeader(live = null) {
    const st = live || await api.get('/api/draft/state', { slot: state.slot });
    clear(header);
    if (st.unavailable) { header.appendChild(unavailableBlock(st)); return st; }
    ctx.draftState = st;

    for (const w of st.warnings || []) header.appendChild(callout('warn', w));

    const tiles = h('div', { class: 'tiles' });
    const until = st.picks_until_my_turn;
    tiles.appendChild(tile({
      label: 'Status', value: st.status === 'pre_draft' ? 'Not started' : st.status,
      sub: `${st.picks_made} of ${st.teams * st.rounds} picks made`,
    }));
    tiles.appendChild(tile({
      label: 'Your slot', value: st.my_slot ?? '—', small: !st.my_slot,
      sub: st.my_slot ? `source: ${st.slot_source}` : 'set it below',
      tone: st.my_slot ? null : 'var(--warning)',
    }));
    const onClock = until === 0;
    tiles.appendChild(tile({
      label: 'Until your turn',
      value: until === null || until === undefined ? '—' : onClock ? 'NOW' : until,
      tone: onClock ? 'var(--critical)' : until !== null && until <= 2 ? 'var(--warning)' : null,
      sub: st.next_pick_overall ? `pick #${st.next_pick_overall} on the clock` : '',
    }));
    tiles.appendChild(tile({
      label: 'Your picks', value: st.my_roster?.length ?? 0,
      sub: st.my_roster?.length ? st.my_roster.map((p) => p.position).join(' ') : 'none yet',
    }));
    header.appendChild(tiles);
    if (onClock) header.querySelector('.tiles').classList.add('oncl');
    return st;
  }

  // Controls that apply to the whole room.
  const controls = h('div', { class: 'filter-row' },
    h('div', { class: 'field' },
      h('label', {}, 'Your draft slot'),
      h('select', {
        onchange: (e) => { state.slot = e.target.value ? Number(e.target.value) : null; render(); },
      },
        h('option', { value: '' }, 'Auto-detect'),
        ...Array.from({ length: ctx.boot.league.teams }, (_, i) =>
          h('option', { value: i + 1, selected: state.slot === i + 1 }, `Slot ${i + 1}`)))),
    h('div', { class: 'spacer' }),
    h('button', {
      class: `btn ${state.live ? 'danger' : ''}`,
      onclick: () => { state.live = !state.live; render(); },
    }, state.live ? '■ Stop live' : '● Go live'),
    h('button', { class: 'btn', onclick: () => refreshHeader() }, 'Refresh'));
  root.insertBefore(controls, tabs);

  // Live SSE wiring. One stream for the whole page.
  if (ctx.stream) { ctx.stream.close(); ctx.stream = null; }
  if (state.live) {
    ctx.stream = api.draftStream(state.slot, {
      onDraft: (st) => {
        const before = ctx.draftState?.picks_made;
        refreshHeader(st);
        if (before !== undefined && st.picks_made !== before && state.tab === 'recommend') {
          toast('Pick made', `${st.picks_made} picks in. Re-running the simulation.`);
          runSim();
        }
        if (st.picks_until_my_turn !== null && st.picks_until_my_turn <= 2) {
          toast('You are up soon', `${st.picks_until_my_turn} picks until your turn.`, 'error');
        }
      },
      onError: (msg) => toast('Live feed error', msg, 'error'),
    });
  }

  await refreshHeader();
  render();

  function render() {
    clear(panel);
    if (state.tab === 'recommend') renderRecommend();
    else if (state.tab === 'board') renderBoard();
    else if (state.tab === 'dissent') renderDissent();
    else if (state.tab === 'plan') renderPlan();
    else if (state.tab === 'recap') renderRecap();
  }

  // -- recommend ------------------------------------------------------------

  function renderRecommend() {
    const controlsRow = h('div', { class: 'filter-row' },
      h('div', { class: 'field' }, h('label', {}, 'Candidates'),
        h('input', { type: 'number', id: 'sim-cand', value: 8, min: 3, max: 16, style: { width: '80px' } })),
      h('div', { class: 'field' }, h('label', {}, 'Simulated drafts each'),
        h('input', { type: 'number', id: 'sim-trials', value: 200, min: 50, max: 1000, step: 50, style: { width: '100px' } })),
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn primary', onclick: runSim }, 'Simulate the rest of the draft'));
    panel.appendChild(controlsRow);

    const out = h('div', { id: 'sim-out' });
    panel.appendChild(out);

    if (state.sim) {
      drawSim(state.sim, out);
    } else {
      // A refresh mid-draft should not lose a result you are still reading, so
      // hydrate from the last completed run before offering to start a new one.
      out.appendChild(loading('Checking for a recent simulation…'));
      api.get('/api/draft/simulate/last').then((prev) => {
        if (!out.isConnected) return;
        clear(out);
        if (!prev.none) {
          state.sim = prev;
          drawSim(prev, out);
        } else if (ctx.params.auto) {
          runSim();                              // deep link: #/draft?auto=1
        } else {
          out.appendChild(empty('▶',
            'Run the simulation',
            'For each pick you could make, this plays all remaining rounds out a few hundred times and ' +
            'scores the roster you end up with. Takes a few seconds.'));
        }
      }).catch(() => {
        clear(out);
        out.appendChild(empty('▶', 'Run the simulation', 'Takes a few seconds.'));
      });
    }

    const picks = ctx.draftState?.recent_picks || [];
    if (picks.length) {
      panel.appendChild(card({
        title: 'Recent picks', flush: true,
        body: table([
          { key: 'pick_no', label: '#', num: true, width: '52px' },
          { key: 'round', label: 'Rd', num: true, width: '48px' },
          { key: 'position', label: 'Pos', render: (r) => posChip(r.position), sortable: false, width: '60px' },
          { key: 'player', label: 'Player' },
          { key: 'team', label: 'Team', width: '70px' },
          { key: 'draft_slot', label: 'Slot', num: true, width: '60px' },
        ], picks, { initialSort: { key: 'pick_no', dir: 'desc' }, maxHeight: '300px' }),
      }));
    }
  }

  async function runSim() {
    const out = document.getElementById('sim-out');
    if (!out) return;
    const cand = Number(document.getElementById('sim-cand')?.value || 8);
    const trials = Number(document.getElementById('sim-trials')?.value || 200);
    clear(out);
    const status = loading('Starting…');
    out.appendChild(status);
    try {
      const res = await api.job('/api/draft/simulate',
        { candidates: cand, trials, slot: state.slot },
        (msg, elapsed) => { status.lastChild.textContent = `${msg} (${elapsed}s)`; });
      state.sim = res;
      clear(out);
      drawSim(res, out);
    } catch (err) {
      clear(out);
      out.appendChild(callout('bad', err.message));
    }
  }

  function drawSim(res, out) {
    if (res.error) {
      out.appendChild(empty('⚠', res.error, res.hint));
      return;
    }
    const recs = res.recommendations || [];
    if (!recs.length) { out.appendChild(empty('—', 'No candidates', 'The draft may be finished.')); return; }

    const top = recs[0];
    const runnerUp = recs[1];

    // The actual decision: regret against survival.
    let advice;
    if (!runnerUp) advice = `<b>${top.player}</b> is the clear pick.`;
    else if (top.survival_to_next_pick > 0.75 && runnerUp.regret_vs_best < 12) {
      advice = `<b>${top.player}</b> is the best available, but he is <b>${Math.round(top.survival_to_next_pick * 100)}% likely to last</b> ` +
        `to your next pick. Taking <b>${runnerUp.player}</b> now costs only ${fmt(runnerUp.regret_vs_best, 0)} points and may get you both.`;
    } else if (top.survival_to_next_pick < 0.2) {
      advice = `<b>${top.player}</b> — he will not be there at your next pick (${Math.round(top.survival_to_next_pick * 100)}% to last), ` +
        `and the next best option costs ${fmt(runnerUp.regret_vs_best, 0)} points.`;
    } else {
      advice = `<b>${top.player}</b> is the pick. Runner-up ${runnerUp.player} is ${fmt(runnerUp.regret_vs_best, 0)} points behind.`;
    }
    out.appendChild(callout('ok', advice, '★'));

    out.appendChild(card({
      title: 'Expected roster strength by pick',
      sub: `${res.trials_per_candidate} simulated drafts each`,
      body: rangeChart(recs.map((r) => ({
        label: r.player.replace(/\s*\((.*)\)/, ' ($1)'),
        value: r.expected_roster_score,
        low: r.p10, high: r.p90,
        color: posColor(r.position),
        rows: [
          ['Expected season points', fmt(r.expected_roster_score, 0)],
          ['Range (p10–p90)', `${fmt(r.p10, 0)} – ${fmt(r.p90, 0)}`],
          ['Behind best', fmt(r.regret_vs_best, 1)],
          ['Lasts to next pick', `${Math.round(r.survival_to_next_pick * 100)}%`],
          ['ADP', r.adp ?? '—'],
        ],
      })), { xTitle: 'Weighted season points from the finished roster', legend: positionLegend(recs) }),
      note: `Evaluating your pick #${res.evaluating_your_pick_at}` +
        (res.your_following_pick ? `, next at #${res.your_following_pick}` : ', your last pick') +
        (res.candidates_dropped_as_unreachable ? ` · ${res.candidates_dropped_as_unreachable} players dropped as unlikely to reach you` : ''),
    }));

    out.appendChild(card({
      title: 'Every candidate', flush: true,
      body: table([
        { key: 'player', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.player.replace(/\s*\(.*/, '')) },
        { key: 'adp', label: 'ADP', num: true },
        { key: 'expected_roster_score', label: 'Expected', num: true, render: (r) => fmt(r.expected_roster_score, 0) },
        { key: 'regret_vs_best', label: 'Regret', num: true,
          render: (r) => h('span', { class: r.regret_vs_best === 0 ? 'up strong' : '' }, fmt(r.regret_vs_best, 1)) },
        { key: 'survival_to_next_pick', label: 'Lasts', num: true,
          render: (r) => {
            const p = Math.round(r.survival_to_next_pick * 100);
            const tone = p >= 70 ? 'up' : p <= 20 ? 'down' : '';
            return h('span', { class: tone }, `${p}%`);
          } },
        { key: 'typical_next_rounds', label: 'Typical next rounds', sortable: false,
          render: (r) => h('span', { class: 'small secondary' }, (r.typical_next_rounds || []).join(' → ')) },
      ], recs, { initialSort: { key: 'expected_roster_score', dir: 'desc' } }),
      note: 'Regret is points behind the best option. Lasts is the chance he survives to your next pick. ' +
        'A small regret with a high Lasts means you can safely wait.',
    }));

    if (res.market_calibration?.calibrated) {
      out.appendChild(callout('ok',
        `Market spread calibrated from this draft: <b>×${res.market_calibration.scale}</b> — ${res.market_calibration.reading}.`));
    }
  }

  // -- board ----------------------------------------------------------------

  async function renderBoard() {
    const filters = h('div', { class: 'filter-row' },
      h('div', { class: 'field' }, h('label', {}, 'Position'),
        h('select', { id: 'board-pos', onchange: renderBoard },
          ...['', 'QB', 'RB', 'WR', 'TE', 'K', 'DEF'].map((p) =>
            h('option', { value: p, selected: (ctx.viewState.boardPos || '') === p }, p || 'All')))),
      h('div', { class: 'spacer' }),
      h('span', { class: 'small secondary' }, 'Value over replacement, from your league\'s own roster slots'));

    const pos = document.getElementById('board-pos')?.value ?? ctx.viewState.boardPos ?? '';
    ctx.viewState.boardPos = pos;

    clear(panel);
    panel.appendChild(filters);
    const holder = h('div', {}, loading('Building the value board…'));
    panel.appendChild(holder);

    try {
      const res = await api.get('/api/board', { position: pos, top: 250 });
      clear(holder);
      const drafted = new Set(ctx.draftState?.drafted_ids || []);
      holder.appendChild(card({
        flush: true,
        title: `${res.count} players`,
        sub: `replacement: ${Object.entries(res.replacement_levels).map(([k, v]) => `${k}${v}`).join(' · ')}`,
        body: table([
          { key: 'overall', label: '#', num: true, width: '54px' },
          { key: 'player', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.player.replace(/\s*\(.*/, '')) },
          { key: 'tier', label: 'Tier', num: true, width: '58px' },
          { key: 'projected_points', label: 'Proj', num: true },
          { key: 'vbd', label: 'VBD', num: true, render: (r) => h('b', {}, fmt(r.vbd, 1)) },
          { key: 'blended', label: 'Blended', num: true, render: (r) => fmt(r.blended, 1) },
          { key: 'adp', label: 'ADP', num: true },
          { key: 'value_vs_adp', label: 'vs ADP', num: true,
            render: (r) => h('span', { class: r.value_vs_adp > 0 ? 'up' : r.value_vs_adp < 0 ? 'down' : '' }, signed(r.value_vs_adp, 0)) },
          { key: 'bye_week', label: 'Bye', num: true, width: '58px' },
          { key: 'playoff_points', label: 'Wk15-17', num: true, render: (r) => fmt(r.playoff_points, 0) },
        ], res.board, { initialSort: { key: 'vbd', dir: 'desc' }, maxHeight: '660px', onRowClick: (r) => ctx.openPlayer(r.player_id) }),
        note: 'Blended pulls our projection 35% toward the market. Wk15-17 is projected fantasy-playoff points.',
      }));
    } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
  }

  // -- dissent --------------------------------------------------------------

  async function renderDissent() {
    const holder = h('div', {}, loading('Comparing projections against ADP…'));
    panel.appendChild(holder);
    try {
      const res = await api.get('/api/draft/dissent', { top: 15 });
      clear(holder);
      holder.appendChild(callout('ok',
        'These are the players where our projections and the market most disagree. Sleeper\'s projections fail in ' +
        'predictable, checkable ways — a rookie with no history, a receiver who just inherited a bigger role, a ' +
        'backfield that resolved in August. Ten minutes of news reading here is the best draft prep available.'));

      const grid = h('div', { class: 'grid c2' });
      grid.appendChild(card({
        title: 'We like them more than the market',
        sub: 'possible value',
        body: barChart(
          (res.we_like_more_than_market || []).map((r) => ({
            label: r.player.replace(/\s*\(.*/, ''), value: r.edge, color: posColor(r.position),
          })),
          { xTitle: 'Picks of daylight', barHeight: 19, labelWidth: 148, valueFormat: (v) => fmt(v, 0),
            legend: positionLegend(res.we_like_more_than_market || []) }),
      }));
      grid.appendChild(card({
        title: 'The market likes them more than we do',
        sub: 'possible trap, or news we missed',
        body: barChart(
          (res.market_likes_more_than_us || []).map((r) => ({
            label: r.player.replace(/\s*\(.*/, ''), value: Math.abs(r.edge), color: 'var(--div-neg)',
          })),
          { xTitle: 'Picks earlier than our value', barHeight: 19, labelWidth: 148, valueFormat: (v) => fmt(v, 0) }),
      }));
      holder.appendChild(grid);

      holder.appendChild(card({
        title: 'Detail', flush: true,
        body: table([
          { key: 'player', label: 'Player', render: (r) => h('span', {}, posChip(r.position), ' ', r.player.replace(/\s*\(.*/, '')) },
          { key: 'our_rank', label: 'Our rank', num: true },
          { key: 'adp', label: 'ADP', num: true },
          { key: 'edge', label: 'Edge', num: true,
            render: (r) => h('b', { class: r.edge > 0 ? 'up' : 'down' }, signed(r.edge, 0)) },
          { key: 'projected_points', label: 'Proj', num: true },
          { key: 'bye_week', label: 'Bye', num: true },
          { key: 'read', label: 'Read', cls: 'wrap', sortable: false,
            render: (r) => h('span', { class: 'small secondary' }, r.read) },
        ], [...(res.we_like_more_than_market || []), ...(res.market_likes_more_than_us || [])],
          { initialSort: { key: 'edge', dir: 'desc' }, onRowClick: (r) => ctx.openPlayer(r.player_id) }),
        note: res.why_excluded,
      }));
    } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
  }

  // -- slot planner ---------------------------------------------------------

  function renderPlan() {
    panel.appendChild(h('div', { class: 'filter-row' },
      h('span', { class: 'small secondary' },
        'Simulates a full draft from every slot. Useful before the order is published — and the typical opening ' +
        'column tells you what shape to arrive with.'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn primary', onclick: runPlan }, 'Simulate all slots')));

    const out = h('div', { id: 'plan-out' });
    panel.appendChild(out);
    if (state.plan) drawPlan(state.plan, out);
    else out.appendChild(empty('▦', 'Plan your slot', 'Runs a full draft from each of the 12 slots.'));
  }

  async function runPlan() {
    const out = document.getElementById('plan-out');
    clear(out);
    const status = loading('Starting…');
    out.appendChild(status);
    try {
      const res = await api.job('/api/draft/plan', { trials: 150 },
        (msg, el2) => { status.lastChild.textContent = `${msg} (${el2}s)`; });
      state.plan = res;
      clear(out);
      drawPlan(res, out);
    } catch (err) { clear(out); out.appendChild(callout('bad', err.message)); }
  }

  function drawPlan(res, out) {
    const rows = res.slots || [];
    out.appendChild(card({
      title: 'Expected roster strength by draft slot',
      body: rangeChart(rows.map((r) => ({
        label: `Slot ${r.slot}  (picks ${r.first_picks.slice(0, 2).join(', ')}…)`,
        value: r.expected_roster_score, low: r.p10, high: r.p90,
        color: state.slot === r.slot ? SERIES(2) : SERIES(1),
        rows: [
          ['Expected', fmt(r.expected_roster_score, 0)],
          ['Typical opening', r.typical_opening.join(' → ')],
          ['First picks', r.first_picks.join(', ')],
        ],
      })), { xTitle: 'Weighted season points', labelWidth: 220 }),
      note: res.note,
    }));

    out.appendChild(card({
      title: 'Typical opening by slot', flush: true,
      body: table([
        { key: 'slot', label: 'Slot', num: true, width: '64px' },
        { key: 'first_picks', label: 'Your first picks', sortable: false,
          render: (r) => h('span', { class: 'mono' }, r.first_picks.join(', ')) },
        { key: 'expected_roster_score', label: 'Expected', num: true, render: (r) => fmt(r.expected_roster_score, 0) },
        { key: 'p10', label: 'p10', num: true, render: (r) => fmt(r.p10, 0) },
        { key: 'p90', label: 'p90', num: true, render: (r) => fmt(r.p90, 0) },
        { key: 'typical_opening', label: 'Typical opening', sortable: false,
          render: (r) => h('span', {}, r.typical_opening.map((p) => posChip(p))) },
      ], rows, { initialSort: { key: 'slot', dir: 'asc' } }),
    }));
  }

  // -- recap ----------------------------------------------------------------

  async function renderRecap() {
    const holder = h('div', {}, loading('Loading the draft recap…'));
    panel.appendChild(holder);
    try {
      const res = await api.get('/api/draft/recap');
      clear(holder);
      if (res.error) return holder.appendChild(empty('—', res.error, 'This fills in once the draft is done.'));
      const totals = Object.entries(res.vbd_by_draft_slot || {});
      if (!totals.length) return holder.appendChild(empty('—', 'No picks yet', 'Come back after the draft.'));
      holder.appendChild(card({
        title: 'Value captured by draft slot',
        body: barChart(totals.map(([slot, v]) => ({
          label: `Slot ${slot}${Number(slot) === ctx.draftState?.my_slot ? ' (you)' : ''}`,
          value: v,
          color: Number(slot) === ctx.draftState?.my_slot ? SERIES(2) : SERIES(1),
        })), { xTitle: 'Total VBD drafted', labelWidth: 120 }),
      }));
    } catch (err) { clear(holder); holder.appendChild(callout('bad', err.message)); }
  }
}
