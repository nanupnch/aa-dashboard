'use strict';
/* AI Model Lineup — reads data/latest.json and data/changes.json, draws everything with inline SVG. */

const SUBS = { 'ChatGPT Plus': 'chatgpt', 'Claude Pro': 'claude', 'Google AI Pro': 'google' };
const PLAN_KEY = 'aa-dashboard:picks-plan';
const LANES = [
  { key: 'intelligence', title: 'Smarter', tip: 'Intelligence Index' },
  { key: 'cost', title: 'Cheaper', tip: 'Cost per task (log scale)' },
  { key: 'time', title: 'Faster', tip: 'Wait before answer (log scale)' },
];
const MEASURE = { intelligence: 'Intelligence', cost: 'Cost/task', time: 'Wait' };
const X0 = 0.06, X1 = 0.94; // lane padding so edge dots stay inside

const state = { picksPlan: 'overall', data: null, events: [], effort: null, sortBy: null, tradeX: 'cost', allChanges: false, showBeaten: false, focus: null, minScore: null, maxTime: null };

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const subKey = (sub) => SUBS[sub] || 'other';
const EFFORT_LABELS = { xhigh: 'XHigh', 'non-reasoning': 'None', default: 'Default' };
const effLabel = (e) => EFFORT_LABELS[e] || (e ? e[0].toUpperCase() + e.slice(1) : '?');

/* ---------- formatting ---------- */
function money(v) {
  const p = Number(v.toPrecision(2));
  if (p >= 10) return '$' + Math.round(p);
  if (p >= 1) return '$' + p.toFixed(1);
  return '$' + p.toFixed(Math.max(2, 1 - Math.floor(Math.log10(p))));
}
function seconds(v) {
  if (v < 10) return v.toFixed(1) + ' s';
  if (v < 60) return Math.round(v) + ' s';
  return Number((v / 60).toPrecision(2)) + ' min';
}
function fmt(key, v) {
  if (v == null) return '—';
  if (key === 'intelligence') return String(Math.round(v));
  return key === 'cost' ? money(v) : seconds(v);
}
function tickLabel(key, v) {
  if (key === 'intelligence') return String(v);
  if (key === 'cost') return '$' + v;
  return v < 60 ? v + ' s' : v / 60 + ' min';
}
function shortDate(iso) {
  const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''));
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
function age(hours) {
  return hours < 48 ? `${Math.max(1, Math.round(hours))} h` : `${Math.round(hours / 24)} d`;
}

/* ---------- scales ---------- */
function pos(key, v) {
  const a = state.data.axes[key];
  const t = key === 'intelligence'
    ? (v - a.min) / (a.max - a.min)
    : (Math.log(a.max) - Math.log(v)) / (Math.log(a.max) - Math.log(a.min)); // lower is better -> right
  return Math.min(1, Math.max(0, t));
}
const pct = (t) => ((X0 + t * (X1 - X0)) * 100).toFixed(2);
function ticks(key, dense = false) {
  const a = state.data.axes[key];
  if (key === 'intelligence') {
    const out = [];
    const step = dense ? 5 : 10;
    for (let v = Math.ceil(a.min / step) * step; v <= a.max; v += step) out.push(v);
    return out;
  }
  const cand = key === 'cost'
    ? (dense ? [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100] : [0.001, 0.01, 0.1, 1, 10, 100])
    : (dense ? [0.3, 1, 3, 10, 30, 60, 180, 600, 1800] : [0.1, 1, 10, 60, 600, 3600]);
  return cand.filter((v) => v >= a.min && v <= a.max);
}
const better = (key, a, b) => (key === 'intelligence' ? a > b : a < b);
function within(key, above, v) {
  const t = state.data.settings.tolerance;
  if (key === 'intelligence') return Math.abs(above - v) <= t.intelligence_abs;
  const rel = key === 'cost' ? t.cost_rel : t.time_rel;
  return Math.abs(v - above) / above <= rel;
}

/* ---------- selection & ranking ---------- */
function effortIdx(e) {
  const i = state.data.settings.effort_order.indexOf(e);
  return i < 0 ? 999 : i;
}
const bySlug = (slug) => state.data.models.flatMap((m) => m.variants).find((v) => v.slug === slug);
const shown = (v) => v.status !== 'unconfirmed' && !v.merged_into && (state.showBeaten || !v.dominated_by);
/* ---------- good-enough thresholds: minimum score and/or maximum wait ---------- */
// Each threshold's stops are computed on their own (the other threshold off), so they never shift.
// Score: a stop wherever the overall $ cheapest / ⚡ fastest pick changes (per-row picks change at almost
// every score). Wait: a stop wherever any model's suggested effort changes (the overall pick barely moves).
const TH = {
  score: { key: 'intelligence', state: 'minScore', other: 'maxTime', trigger: 'overall' }, // any (left) → higher
  time: { key: 'time', state: 'maxTime', other: 'minScore', trigger: 'rows' },             // fastest (left) → any (right)
};
const thresholdOn = () => state.minScore != null || state.maxTime != null;
function qualifies(v) {
  if (state.minScore != null && !(v.intelligence != null && v.intelligence >= state.minScore)) return false;
  if (state.maxTime != null && !(v.time != null && v.time <= state.maxTime)) return false;
  return true;
}
const below = (v) => thresholdOn() && !qualifies(v);
const candidates = () => state.data.models.flatMap((m) => m.variants).filter((v) => v.status !== 'unconfirmed' && !v.merged_into);
// The cheapest effort of this model that meets every active threshold.
function pickGoodEnough(model) {
  const vs = model.variants.filter((v) => v.status !== 'unconfirmed' && !v.merged_into);
  const ok = vs.filter(qualifies)
    .sort((a, b) => (a.cost ?? Infinity) - (b.cost ?? Infinity) || effortIdx(a.effort) - effortIdx(b.effort));
  if (ok.length) return { v: ok[0], meets: true };
  const fb = state.minScore != null
    ? vs.filter((v) => v.intelligence != null).sort((a, b) => b.intelligence - a.intelligence)[0]
    : vs.filter((v) => v.time != null).sort((a, b) => a.time - b.time)[0];
  return { v: fb || null, meets: false };
}
// The overall suggestion: cheapest and fastest among each model's good-enough pick.
function bestPicks() {
  const ok = state.data.models.map((m) => ({ m, ...pickGoodEnough(m) })).filter((r) => r.meets && r.v);
  const best = (k) => ok.filter((r) => r.v[k] != null).sort((a, b) => a.v[k] - b.v[k])[0];
  return { cheap: best('cost'), fast: best('time') };
}
// Walk the values from loose to strict and keep the loosest value of each run with the same suggestion.
function computeStops(name) {
  const th = TH[name];
  const vals = [...new Set(candidates().map((v) => v[th.key]).filter((x) => x != null))]
    .sort((a, b) => (name === 'score' ? a - b : b - a));
  const saved = state[th.state], savedOther = state[th.other];
  state[th.other] = null;
  const stops = [];
  let prev = null;
  for (const x of vals) {
    state[th.state] = x;
    let sig;
    if (th.trigger === 'overall') { const b = bestPicks(); sig = `${b.cheap?.v.slug}|${b.fast?.v.slug}`; }
    else sig = state.data.models.map((m) => { const p = pickGoodEnough(m); return p.meets ? p.v.slug : '-'; }).join(',');
    if (sig !== prev) { stops.push(x); prev = sig; }
  }
  state[th.state] = saved;
  state[th.other] = savedOther;
  th.values = vals;                          // strictness order
  th.stops = stops.sort((a, b) => a - b);    // ascending, as on the slider
}
// Slider index of the stop equivalent to threshold t. Score: 0 = any, i = stops[i-1]. Time: i = stops[i], n = any.
function sliderIndex(name, t) {
  const th = TH[name], st = th.stops, n = st.length;
  if (name === 'score') {
    if (t == null) return 0;
    const x = th.values.find((v) => v >= t);
    if (x == null) return n;
    let i = 0;
    st.forEach((s, k) => { if (s <= x) i = k + 1; });
    return i;
  }
  if (t == null) return n;
  const x = th.values.find((v) => v <= t);
  if (x == null) return 0;
  const k = st.findIndex((s) => s >= x);
  return k < 0 ? n - 1 : k;
}
function valueAt(name, i) {
  const st = TH[name].stops, n = st.length;
  if (name === 'score') return i <= 0 ? null : st[Math.min(i, n) - 1];
  return i >= n ? null : st[Math.max(0, i)];
}
function pick(model) {
  if (thresholdOn()) return pickGoodEnough(model).v;
  // Closest effort to the selected one (exact, then lower, then higher). A merged variant resolves to
  // its cheaper twin; a beaten one is skipped while beaten variants are hidden.
  const vs = model.variants.filter((v) => v.status !== 'unconfirmed');
  if (!vs.length) return null;
  const rep = (v) => (v.merged_into ? bySlug(v.merged_into) : v);
  const want = effortIdx(state.effort);
  const rank = (v) => { const d = effortIdx(v.effort) - want; return d === 0 ? 0 : d < 0 ? -d : 100 + d; };
  const order = vs.slice().sort((a, b) => rank(a) - rank(b));
  for (const v of order) { const r = rep(v); if (shown(r)) return r; }
  return rep(order[0]); // every variant is beaten: show the closest anyway
}
function pickReason(model, v) {
  const exact = model.variants.find((x) => x.effort === state.effort);
  if (!exact) return 'Closest available effort';
  if (exact.merged_into === v.slug) return 'Same score within noise, cheaper';
  return 'Selected effort is beaten; closest that isn\'t';
}
const effName = (v) => effLabel(v.effort) + (v.merged.length ? ' ≈ ' + v.merged.map(effLabel).join(' ≈ ') : '');
function rankRows(rows, key) {
  const val = (r) => (r.v ? r.v[key] : null);
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null || y == null || x === y) {
      if (x == null && y != null) return 1;
      if (y == null && x != null) return -1;
      return 0; // keep tier order
    }
    return better(key, x, y) ? -1 : 1;
  });
  let prev = null, rank = 0;
  rows.forEach((r, i) => {
    const x = val(r);
    r.tie = false;
    if (x == null) { r.rank = null; return; }
    if (prev != null && within(key, prev, x)) { r.rank = rank; r.tie = true; rows[i - 1].tie = true; }
    else { rank = i + 1; r.rank = rank; }
    prev = x;
  });
}

/* ---------- lineup ---------- */
function dotClass(v) {
  if (v.status === 'unconfirmed') return 'unconf';
  return [v.dominated_by ? 'hollow' : '', v.status === 'stale' ? 'stale' : ''].join(' ').trim();
}
function laneSvg(m, sel, key) {
  const CY = 31; // dots sit low; the selected value is printed above them so it never covers a dot
  const grid = ticks(key, !!state.focus).map((t) => { const x = pct(pos(key, t)); return `<line class="grid" x1="${x}%" x2="${x}%" y1="22" y2="40"/>`; }).join('');
  const pts = m.variants.filter((v) => v[key] != null && (v === sel || shown(v))).map((v) => ({ v, x: pct(pos(key, v[key])) }));
  const solid = pts.filter((p) => p.v.status !== 'unconfirmed');
  let links = '';
  for (let i = 1; i < solid.length; i++) links += `<line class="link" x1="${solid[i - 1].x}%" x2="${solid[i].x}%" y1="${CY}" y2="${CY}"/>`;
  const dots = pts.filter((p) => p.v !== sel).map((p) => `<circle class="dot ${dotClass(p.v)}${below(p.v) ? ' below' : ''}" cx="${p.x}%" cy="${CY}" r="4"/>`).join('');
  const tv = key === 'intelligence' ? state.minScore : key === 'time' ? state.maxTime : null;
  const tline = tv != null ? `<line class="thresh-line" x1="${pct(pos(key, tv))}%" x2="${pct(pos(key, tv))}%" y1="20" y2="42"/>` : '';
  let big = '';
  if (sel && sel[key] != null) {
    const t = pos(key, sel[key]);
    const x = pct(t);
    const anchor = t < 0.15 ? 'start' : t > 0.85 ? 'end' : 'middle';
    const dx = anchor === 'start' ? -6 : anchor === 'end' ? 6 : 0;
    const text = (sel.clamped.includes(key) ? (t > 0.5 ? '▸ ' : '◂ ') : '') + fmt(key, sel[key]);
    big = `<circle class="dot sel ${dotClass(sel)}" cx="${x}%" cy="${CY}" r="6.5"/>`
      + `<text class="val" x="${x}%" y="17" dx="${dx}" text-anchor="${anchor}">${esc(text)}</text>`;
  } else if (sel) {
    big = `<text class="na" x="50%" y="17" text-anchor="middle">—</text>`;
  }
  const hits = pts.map((p) => `<circle class="hit" cx="${p.x}%" cy="${CY}" r="11" data-model="${esc(m.key)}" data-slug="${esc(p.v.slug)}" data-lane="${key}"/>`).join('');
  return `<div class="lane"><svg class="lane-svg" aria-hidden="true">${grid}${tline}${links}${dots}${big}${hits}</svg></div>`;
}
const lanes = () => (state.focus ? LANES.filter((l) => l.key === state.focus) : LANES);
function headRow() {
  const narrow = innerWidth < 720 && !state.focus;
  const cells = lanes().map((l) => {
    const pressed = state.focus === l.key;
    const axis = ticks(l.key, !!state.focus).filter((_, i) => !narrow || i % 2 === 0).map((t) => {
      const p = pos(l.key, t);
      const anchor = p < 0.08 ? 'start' : p > 0.92 ? 'end' : 'middle';
      return `<text x="${pct(p)}%" y="11" text-anchor="${anchor}" dx="${anchor === 'start' ? -4 : anchor === 'end' ? 4 : 0}">${esc(tickLabel(l.key, t))}</text>`;
    }).join('');
    return `<div class="lane-head"><button type="button" data-focus="${l.key}" aria-pressed="${pressed}" title="${pressed ? 'Show all three' : l.tip + ' · tap to expand and sort'}">${l.title} →${pressed ? '<span class="x" aria-label="close">✕</span>' : ''}</button>`
      + `<svg class="axis" aria-hidden="true">${axis}</svg></div>`;
  }).join('');
  return `<div class="row head"><div class="name"></div>${cells}</div>`;
}
function rowHtml(r) {
  const m = r.model, v = r.v, sk = subKey(m.subscription);
  const rank = state.sortBy && r.rank ? `<span class="rank" title="${r.tie ? 'Tie within noise' : 'Rank'}">${r.tie ? '≈' : ''}${r.rank}</span>` : '';
  const gm = thresholdOn();
  const effNote = v && (gm || v.effort !== state.effort)
    ? ` <span class="eff" title="${gm ? (r.meets ? 'Cheapest effort that meets the thresholds' : 'Never meets the thresholds; closest effort shown') : pickReason(m, v)}">${effLabel(v.effort)}</span>` : '';
  const badges = (r.badges || []).map((b) => { const w = b === '$' ? 'Cheapest' : 'Fastest'; return `<span class="pick-badge" title="${w} model that's good enough" aria-label="${w}">${b}</span>`; }).join('');
  const aria = v ? `${m.display_name} (${m.subscription}), ${effLabel(v.effort)}: intelligence ${fmt('intelligence', v.intelligence)}, ${fmt('cost', v.cost)} per task, ${fmt('time', v.time)} wait` : m.display_name;
  const dim = (gm ? !r.meets : v && v.dominated_by && !state.showBeaten) ? ' dim' : '';
  return `<div class="row${dim}" role="button" tabindex="0" data-key="${esc(m.key)}" style="--c:var(--${sk})" aria-label="${esc(aria)}" title="${esc(m.subscription)} · ${esc(m.tier_role)}">`
    + `<div class="name">${rank}${glyph(m)}<b>${esc(m.display_name)}</b>${effNote}${badges}</div>`
    + lanes().map((l) => laneSvg(m, v, l.key)).join('') + '</div>';
}
const TIER_ORDER = ['Largest', 'Flagship', 'Mid', 'Small'];
const tierIdx = (t) => (TIER_ORDER.includes(t) ? TIER_ORDER.indexOf(t) : TIER_ORDER.length);
const TIER_SHAPE = { Largest: 'diamond', Flagship: 'circle', Mid: 'square', Small: 'triangle' };
function shape(tier, cx, cy, r, cls = '', style = '') {
  const s = TIER_SHAPE[tier] || 'circle';
  const at = `class="${cls}"${style ? ` style="${style}"` : ''}`;
  const f = (n) => n.toFixed(1);
  if (s === 'square') { const h = r * 0.88; return `<rect ${at} x="${f(cx - h)}" y="${f(cy - h)}" width="${f(2 * h)}" height="${f(2 * h)}" rx="1"/>`; }
  if (s === 'diamond') { const h = r * 1.25; return `<polygon ${at} points="${f(cx)},${f(cy - h)} ${f(cx + h)},${f(cy)} ${f(cx)},${f(cy + h)} ${f(cx - h)},${f(cy)}"/>`; }
  if (s === 'triangle') { const h = r * 1.2; return `<polygon ${at} points="${f(cx)},${f(cy - h)} ${f(cx + h)},${f(cy + h * 0.8)} ${f(cx - h)},${f(cy + h * 0.8)}"/>`; }
  return `<circle ${at} cx="${f(cx)}" cy="${f(cy)}" r="${f(r)}"/>`;
}
const glyph = (m) => `<svg class="glyph" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style="--c:var(--${subKey(m.subscription)})">${shape(m.tier_role, 7, 7, 4.6, 'gdot')}</svg>`;
function renderLineup() {
  // One flat list, largest tier first; sorting (via an expanded lane) ranks across all models.
  const rows = state.data.models.slice().sort((a, b) => tierIdx(a.tier_role) - tierIdx(b.tier_role))
    .map((model) => ({ model, v: pick(model), meets: !thresholdOn() || pickGoodEnough(model).meets }));
  if (state.sortBy) rankRows(rows, state.sortBy);
  if (thresholdOn()) {
    // Good-enough models first (cheapest first unless a lane sort is active), the rest after, dimmed.
    if (!state.sortBy) rows.sort((a, b) => (a.v?.cost ?? Infinity) - (b.v?.cost ?? Infinity));
    rows.sort((a, b) => Number(b.meets) - Number(a.meets));
    const bp = bestPicks();
    for (const r of rows) {
      r.badges = [];
      if (bp.cheap && bp.cheap.m === r.model) r.badges.push('$');
      if (bp.fast && bp.fast.m === r.model) r.badges.push('⚡');
    }
  }
  const html = headRow() + rows.map(rowHtml).join('');
  $('#lineup').innerHTML = html;
  $('#lineup').classList.toggle('focused', !!state.focus);
  const nBeaten = state.data.models.flatMap((m) => m.variants).filter((v) => v.dominated_by && !v.merged_into).length;
  const tb = $('#beaten');
  tb.setAttribute('aria-pressed', String(state.showBeaten));
  $('#beaten-n').textContent = nBeaten;
  tb.hidden = !nBeaten;
}
function renderEfforts() {
  const present = new Set(state.data.models.flatMap((m) => m.variants.map((v) => v.effort)));
  const efforts = state.data.settings.effort_order.filter((e) => present.has(e) && e !== 'non-reasoning');
  $('#effort-ctl').classList.toggle('on', !thresholdOn());
  $('#efforts').innerHTML = efforts.map((e) => `<button type="button" data-effort="${e}" aria-pressed="${!thresholdOn() && e === state.effort}"${e === 'non-reasoning' ? ' title="No reasoning"' : ''}>${effLabel(e)}</button>`).join('');
}

/* ---------- table view & detail ---------- */
function noteFor(v) {
  const n = [];
  if (v.status === 'unconfirmed') n.push('unpinned');
  if (v.merged_into) n.push(`≈ ${effLabel(bySlug(v.merged_into).effort)}, cheaper`);
  if (v.status === 'stale') n.push(`as of ${shortDate(v.as_of)}`);
  if (v.dominated_by) n.push(`beaten by ${v.dominated_by.label.replace(/\((\w[\w-]*)\)$/, (_, e) => effLabel(e))}`);
  if (v.clamped.length) n.push('off scale');
  return n.join(' · ');
}
function variantTable(rows, withModel) {
  const head = `<thead><tr>${withModel ? '<th></th>' : ''}<th>Effort</th><th class="num">Intel.</th><th class="num">$/task</th><th class="num">Wait</th><th class="num">In $/M</th><th class="num">Out $/M</th><th></th></tr></thead>`;
  let last = null;
  const body = rows.map(({ m, v }) => {
    const first = withModel && m !== last;
    last = m;
    return `<tr class="${first ? 'first' : ''}${v.dominated_by || v.merged_into ? ' beaten' : ''}">${withModel ? `<td>${first ? `<span class="swatch" style="--c:var(--${subKey(m.subscription)})"></span>${esc(m.display_name)}` : ''}</td>` : ''}`
      + `<td>${effLabel(v.effort)}</td><td class="num">${fmt('intelligence', v.intelligence)}</td><td class="num">${fmt('cost', v.cost)}</td><td class="num">${fmt('time', v.time)}</td>`
      + `<td class="num">${v.prices.input == null ? '—' : '$' + v.prices.input}</td><td class="num">${v.prices.output == null ? '—' : '$' + v.prices.output}</td>`
      + `<td class="note">${esc(noteFor(v))}</td></tr>`;
  }).join('');
  return `<div class="tbl-wrap"><table>${head}<tbody>${body}</tbody></table></div>`;
}
function capBars(m) {
  const caps = [...new Set(m.variants.flatMap((v) => Object.keys(v.capabilities)))].sort();
  if (!caps.length) return '';
  const name = (k) => k.replace(/^artificial_analysis_/, '').replace(/_index$/, '').replace(/_and_/g, ' & ').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
  const ax = state.data.axes.intelligence;
  const x = (val) => pct(Math.min(1, Math.max(0, (val - ax.min) / (ax.max - ax.min))));
  // One strip per capability; one dot per effort, same scale and effort-joining line as the Lineup.
  const rows = caps.map((c) => {
    const pts = m.variants.filter((v) => v.capabilities[c] != null && !v.merged_into).map((v) => ({ v, x: x(v.capabilities[c]) }));
    let links = '';
    for (let i = 1; i < pts.length; i++) links += `<line class="link" x1="${pts[i - 1].x}%" x2="${pts[i].x}%" y1="10" y2="10"/>`;
    const top = pts[pts.length - 1];
    const dots = pts.map((p) => `<circle class="dot${p === top ? ' sel' : ''}" cx="${p.x}%" cy="10" r="${p === top ? 5 : 3.5}"><title>${effLabel(p.v.effort)}: ${Math.round(p.v.capabilities[c])}</title></circle>`).join('');
    return `<div class="cap"><span>${esc(name(c))}</span><svg class="cap-svg" style="--c:var(--${subKey(m.subscription)})">${links}${dots}</svg><b>${top ? Math.round(top.v.capabilities[c]) : '—'}</b></div>`;
  }).join('');
  return `<h3>Capabilities</h3><div class="caps">${rows}</div>`;
}
function openDetail(key) {
  const m = state.data.models.find((x) => x.key === key);
  if (!m) return;
  const dlg = $('#detail');
  $('#detail-h').innerHTML = `<span class="swatch" style="--c:var(--${subKey(m.subscription)})"></span>${esc(m.display_name)} <span class="muted">${esc(m.tier_role)}</span>`;
  $('.dlg-body', dlg).innerHTML = (m.notes ? `<blockquote>${esc(m.notes)}</blockquote>` : '')
    + variantTable(m.variants.map((v) => ({ m, v })), false)
    + capBars(m);
  dlg.showModal();
}

/* ---------- tooltip ---------- */
const tip = $('#tip');
function findVariant(modelKey, slug) {
  const m = state.data.models.find((x) => x.key === modelKey);
  return m ? { m, v: m.variants.find((x) => x.slug === slug) } : {};
}
function showTip(el, x, y) {
  const { m, v } = findVariant(el.dataset.model, el.dataset.slug);
  if (!v) return;
  const lanes = el.dataset.lane ? [el.dataset.lane] : ['intelligence', 'cost', 'time'];
  const note = noteFor(v);
  tip.innerHTML = `<b>${esc(m.display_name)} · ${esc(effName(v))}</b><br>`
    + lanes.map((k) => `${MEASURE[k]} ${fmt(k, v[k])}`).join('<br>')
    + (note ? `<br><span>${esc(note)}</span>` : '');
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.max(8, Math.min(x + 12, innerWidth - r.width - 8)) + 'px';
  tip.style.top = Math.max(8, Math.min(y + 12, innerHeight - r.height - 8)) + 'px';
}
const hideTip = () => { tip.hidden = true; };

/* ---------- trade-off chart ---------- */
function renderTradeoff() {
  const d = state.data, xKey = state.tradeX;
  const W = 720, H = 400, L = 44, R = 20, T = 14, B = 46;
  const ax = d.axes.intelligence;
  const xs = (t) => L + t * (W - L - R);
  const ys = (v) => H - B - Math.min(1, Math.max(0, (v - ax.min) / (ax.max - ax.min))) * (H - T - B);
  let g = '';
  for (const t of ticks(xKey)) { const x = xs(pos(xKey, t)); g += `<line class="grid" x1="${x}" x2="${x}" y1="${T}" y2="${H - B}"/><text x="${x}" y="${H - B + 16}" text-anchor="middle">${esc(tickLabel(xKey, t))}</text>`; }
  for (const t of ticks('intelligence')) { const y = ys(t); g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${t}</text>`; }
  g += `<text class="axis-title" x="${W - R}" y="${H - 8}" text-anchor="end">${xKey === 'cost' ? 'Cheaper' : 'Faster'} →</text>`;
  g += `<text class="axis-title" x="${L}" y="${T - 2}" text-anchor="start" dy="10" dx="6">↑ Smarter</text>`;

  const all = [];
  let lines = '', dots = '', hits = '';
  for (const m of d.models) {
    const sk = subKey(m.subscription);
    const pts = m.variants.filter((v) => shown(v) && v.intelligence != null && v[xKey] != null)
      .map((v) => ({ m, v, sk, x: xs(pos(xKey, v[xKey])), y: ys(v.intelligence) }));
    if (!pts.length) continue;
    all.push(...pts);
    lines += `<polyline class="mline" style="--c:var(--${sk})" points="${pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')}"/>`;
  }
  // Golden quadrant: smarter than the median variant AND better than the median on the x measure.
  let quad = '';
  if (all.length > 2) {
    const med = (a) => { const s = a.slice().sort((p, q) => p - q); const n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
    const mx = med(all.map((p) => p.x)), my = med(all.map((p) => p.y));
    quad = `<rect class="quad" x="${mx.toFixed(1)}" y="${T}" width="${(W - R - mx).toFixed(1)}" height="${(my - T).toFixed(1)}"><title>Golden quadrant: smarter and ${xKey === 'cost' ? 'cheaper' : 'faster'} than the median variant</title></rect>`
      + `<line class="median" x1="${mx.toFixed(1)}" x2="${mx.toFixed(1)}" y1="${T}" y2="${H - B}"/><line class="median" x1="${L}" x2="${W - R}" y1="${my.toFixed(1)}" y2="${my.toFixed(1)}"/>`
      + `<text class="quad-lbl" x="${W - R - 6}" y="${T + 14}" text-anchor="end">★</text>`;
  }
  // Pareto line: variants no other variant (any plan) beats on both shown axes.
  const front = all.filter((p) => !all.some((q) => q !== p && q.v.intelligence >= p.v.intelligence && q.x >= p.x
    && (q.v.intelligence > p.v.intelligence || q.x > p.x))).sort((a, b) => a.x - b.x);
  let thresh = '', ring = '';
  if (state.minScore != null) {
    const y = ys(state.minScore);
    thresh += `<line class="thresh-line" x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/><text class="thresh-lbl" x="${L + 6}" y="${(y - 5).toFixed(1)}">≥ ${state.minScore}</text>`;
  }
  if (state.maxTime != null && xKey === 'time') {
    const x = xs(pos('time', state.maxTime));
    thresh += `<line class="thresh-line" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${T}" y2="${H - B}"/><text class="thresh-lbl" x="${(x + 5).toFixed(1)}" y="${T + 12}">≤ ${esc(seconds(state.maxTime))}</text>`;
  }
  if (thresholdOn()) {
    const okPts = all.filter((p) => qualifies(p.v));
    const bestPt = okPts.sort((a, b) => b.x - a.x)[0]; // furthest right = cheapest / fastest
    if (bestPt) ring = `<circle class="ring" cx="${bestPt.x.toFixed(1)}" cy="${bestPt.y.toFixed(1)}" r="11"/>`;
  }
  let pareto = '';
  if (front.length > 1) {
    pareto = `<polyline class="pareto" points="${front.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')}"><title>Pareto line: best intelligence for each ${xKey === 'cost' ? 'cost' : 'wait'}</title></polyline>`;
  }
  for (const p of all) {
    dots += shape(p.m.tier_role, p.x, p.y, 5.5, `dot ${dotClass(p.v)}${below(p.v) ? ' below' : ''}`, `--c:var(--${p.sk})`);
    hits += `<circle class="hit" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="11" data-model="${esc(p.m.key)}" data-slug="${esc(p.v.slug)}"/>`;
  }
  $('#tradeoff').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Intelligence against ${xKey === 'cost' ? 'cost per task' : 'wait before answer'} for every variant; values are in the table.">${quad}${g}${pareto}${lines}${thresh}${dots}${ring}${hits}</svg>`;
  const tiers = TIER_ORDER.filter((t) => d.models.some((m) => m.tier_role === t));
  $('#trade-key').innerHTML = tiers.map((t) => `<span class="k"><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">${shape(t, 7, 7, 4.6, 'kdot')}</svg>${esc(t)}</span>`).join('');
  document.querySelectorAll('#trade-x button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.x === xKey)));
}

/* ---------- changes ---------- */
function eventHtml(e) {
  const who = `<b>${esc(e.model)}</b>${e.effort ? ' ' + esc(effLabel(e.effort)) : ''}`;
  switch (e.type) {
    case 'variant_added': return `<span class="ev add" title="Added">+</span>${who}`;
    case 'variant_removed': return `<span class="ev rem" title="Removed">−</span>${who}`;
    case 'new_model': return `<span class="ev new" title="New model, not on the slate">★</span><b>${esc(e.model)}</b> <span class="muted">${esc(e.creator)}</span>`;
    case 'index_version': return `<span class="ev warn" title="Scores before and after aren't comparable">⟳</span>Index v${esc(e.old)} → v${esc(e.new)}`;
    case 'measure_changed': {
      const up = e.old != null && e.new != null && better(e.measure, e.new, e.old);
      return `<span class="ev ${up ? 'add' : 'rem'}" title="${up ? 'Better' : 'Worse'}">${up ? '▲' : '▼'}</span>${who} <span class="muted">${MEASURE[e.measure]}</span> ${fmt(e.measure, e.old)} → ${fmt(e.measure, e.new)}`;
    }
    default: return esc(e.type);
  }
}
function renderChanges() {
  const cutoff = new Date(state.data.data_date + 'T12:00:00');
  cutoff.setDate(cutoff.getDate() - 30);
  const iso = cutoff.toISOString().slice(0, 10);
  const recent = state.events.filter((e) => e.date >= iso);
  const list = state.allChanges ? state.events : recent;
  $('#changes-sec').hidden = !list.length;
  const days = new Map();
  for (const e of list) { if (!days.has(e.date)) days.set(e.date, []); days.get(e.date).push(e); }
  $('#changes').innerHTML = [...days].map(([date, evs]) => `<div class="day"><time datetime="${esc(date)}">${esc(shortDate(date))}</time><ul>${evs.map((e) => `<li>${eventHtml(e)}</li>`).join('')}</ul></div>`).join('');
  $('#more-changes').hidden = state.allChanges || state.events.length === recent.length;
}

/* ---------- health ---------- */
const CREATOR_PLAN = { OpenAI: 'ChatGPT Plus', Anthropic: 'Claude Pro', Google: 'Google AI Pro' };
const slugify = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const pre = (t) => `<pre><code>${esc(t)}</code></pre>`;
// Each item: one-line summary; expanding shows what it means and what to do about it.
function healthItem(icon, cls, summary, body) {
  return `<li><details><summary><span class="ev ${cls}">${icon}</span>${summary}</summary><div class="why">${body}</div></details></li>`;
}
function warningBody(w) {
  if (/off scale/.test(w)) {
    return '<p>This value is outside the fixed axis range, so its dot is pinned to the lane edge (▸ / ◂) and its position is not to scale.</p>'
      + '<p>Widen the range for this measure in <code>config/axes.json</code>, leaving some headroom. The ranges stay fixed on purpose, so dots only move when the data does.</p>';
  }
  return '<p>Artificial Analysis has not published this value for this variant, so it shows as "—".</p>'
    + '<p>Without it, the variant can\'t be compared on that measure: it is never marked beaten, never merged with a close-scoring effort, and only chosen by the score / wait filters when nothing else qualifies. No action is needed; it fills in when the data does.</p>';
}
function renderHealth() {
  const h = state.data.health;
  const items = [
    ...h.candidates.map((c) => {
      const plan = CREATOR_PLAN[c.creator] || 'ChatGPT Plus | Claude Pro | Google AI Pro';
      const entry = JSON.stringify({ key: slugify(c.name), display_name: c.name, subscription: plan,
        tier_role: 'Flagship | Mid | Small', aa_name: c.suggested_name, slugs: [], my_notes: '' }, null, 2);
      return healthItem('★', 'new', `<b>${esc(c.name)}</b> <span class="muted">${esc(c.creator)} · ${esc(shortDate(c.release_date))}</span>`,
        `<p>A new model from ${esc(c.creator)}, released after the newest one on your slate. It isn't on the dashboard yet. Artificial Analysis lists ${c.slugs.length} variant${c.slugs.length > 1 ? 's' : ''}: ${c.slugs.map((x) => `<code>${esc(x)}</code>`).join(' ')}.</p>`
        + `<p><b>To track it</b>, add this to <code>models</code> in <code>config/slate.json</code> (check the plan and tier), then pin its slugs:</p>${pre(entry)}${pre('AA_API_KEY=… python3 scripts/discover.py --write')}`
        + `<p><b>To stop seeing it</b>, add <code>"${esc(c.name)}"</code> to <code>ignored</code> in the same file.</p>`);
    }),
    ...h.unconfirmed.map((u) => healthItem('?', 'warn', `${esc(u.name)} <code>${esc(u.slug)}</code>`,
      `<p>Artificial Analysis now lists this variant, but its slug isn't pinned in the slate. It shows as a grey dot and is left out of the filters, merging, beaten marks and quick picks until you confirm it.</p>`
      + `<p><b>To confirm</b>, add <code>"${esc(u.slug)}"</code> to the <code>slugs</code> of <code>${esc(u.key)}</code> in <code>config/slate.json</code>.</p>`)),
    ...h.missing.map((x) => healthItem('−', 'rem', `${esc(x.model)} <code>${esc(x.slug)}</code> <span class="muted">${x.last_seen ? 'since ' + esc(shortDate(x.last_seen)) : 'never seen'}</span>`,
      x.last_seen
        ? `<p>This pinned variant isn't in today's data. The page keeps showing its values from ${esc(shortDate(x.last_seen))}, faded. It may have been renamed or retired.</p><p>Run <code>python3 scripts/discover.py</code> to see the model's current rows, then update or remove the slug in <code>config/slate.json</code>.</p>`
        : `<p>This slug is pinned but has never appeared in the data, so it's probably a typo or an old name.</p><p>Run <code>python3 scripts/discover.py</code> and copy the correct slug into <code>config/slate.json</code>.</p>`)),
    ...h.not_tracked.map((n) => healthItem('∅', '', esc(n),
      `<p>Artificial Analysis doesn't publish results for this model, so it's left off the page. It appears automatically once they start tracking it, as long as its name in <code>config/slate.json</code> matches theirs.</p>`)),
    ...h.warnings.map((w) => healthItem('!', 'warn', esc(w), warningBody(w))),
  ];
  $('#health-sec').hidden = !items.length;
  $('#health-count').textContent = items.length || '';
  $('#health').innerHTML = items.join('');
}

/* ---------- header ---------- */
function renderHeader() {
  const d = state.data;
  const hrs = (Date.now() - Date.parse(d.last_checked)) / 36e5;
  const s = d.settings.stale_hours;
  const badge = $('#badge');
  const date = shortDate(d.data_date);
  badge.title = `Checked ${age(hrs)} ago`;
  if (!(hrs >= 0) && !(hrs < 0)) { badge.className = 'badge red'; badge.textContent = '■ ' + date; }
  else if (hrs < s.amber) { badge.className = 'badge fresh'; badge.textContent = '● ' + date; }
  else if (hrs < s.red) { badge.className = 'badge amber'; badge.textContent = `▲ ${date} · ${age(hrs)} old`; }
  else { badge.className = 'badge red'; badge.textContent = `■ ${date} · ${age(hrs)} old`; }
  $('#meta').textContent = `Index v${d.index_version}`;
  const plans = [...new Set(d.models.map((m) => m.subscription))];
  $('#plans').innerHTML = plans.map((p) => `<span class="plan" style="--c:var(--${subKey(p)})">${esc(p)}</span>`).join('');
  $('#demo').hidden = !d.demo;
  $('#foot-line').innerHTML = `Data: <a href="https://artificialanalysis.ai/" rel="noopener">Artificial Analysis</a>`;
}

function syncThresholds() {
  for (const name of Object.keys(TH)) {
    const th = TH[name], v = state[th.state], on = v != null;
    const wrap = document.querySelector(`.thresh[data-th="${name}"]`);
    const r = wrap.querySelector('.th-range');
    r.max = th.stops.length;
    r.value = sliderIndex(name, v);
    const n = wrap.querySelector('.th-num');
    if (document.activeElement !== n) n.value = on ? v : '';
    wrap.querySelector('.reset').hidden = !on;
    wrap.classList.toggle('on', on);
  }
}
// Set one threshold (null / ≤ 0 = off; capped at its strictest stop), then redraw.
function setThreshold(name, v) {
  const th = TH[name], st = th.stops;
  let val = v == null || !Number.isFinite(v) || v <= 0 ? null : Math.round(v * 100) / 100;
  if (val != null && st.length) val = name === 'score' ? Math.min(val, st[st.length - 1]) : Math.max(val, st[0]);
  state[th.state] = val;
  syncThresholds();
  renderEfforts();
  renderLineup();
  renderTradeoff();
}
function clearThresholds() {
  state.minScore = null;
  state.maxTime = null;
  computeStops('score');
  computeStops('time');
  syncThresholds();
}

/* ---------- quick picks ---------- */
const PICK_SHORT = { smartest: 'Smartest', value: 'Value', fast: 'Fast+smart', budget: 'Budget' };
function renderPicks() {
  // Overall, or within one plan (allowances are per plan). One compact row per winning variant:
  // labels | model + effort | score · cost · wait (aligned columns).
  const plans = state.data.plan_picks || [];
  if (!plans.some((p) => p.plan === state.picksPlan)) state.picksPlan = 'overall';
  const tabs = [{ id: 'overall', label: 'Overall', title: 'All plans' },
    ...plans.map((p) => ({ id: p.plan, label: p.plan.split(' ')[0], title: p.plan, sk: subKey(p.plan) }))];
  $('#picks-plan').hidden = !plans.length;
  $('#picks-plan').innerHTML = tabs.map((t) => `<button type="button" data-plan="${esc(t.id)}" aria-pressed="${t.id === state.picksPlan}" title="${esc(t.title)}"${t.sk ? ` style="--c:var(--${t.sk})"` : ''}>${t.sk ? '<span class="pbar" aria-hidden="true"></span>' : ''}${esc(t.label)}</button>`).join('');
  const qp = (state.picksPlan === 'overall' ? state.data.quick_picks : plans.find((p) => p.plan === state.picksPlan).picks) || [];
  $('#picks-sec').hidden = !(state.data.quick_picks || []).length;
  $('#picks').innerHTML = qp.map((e) => {
    const m = state.data.models.find((x) => x.key === e.model);
    const v = m && m.variants.find((x) => x.slug === e.slug);
    if (!v) return '';
    const why = e.picks.map((p) => `${p.label}: ${p.why}`).join('\n')
      + (e.beats ? `\nBeats ${e.beats} other variant${e.beats > 1 ? 's' : ''} on all three measures` : '');
    return `<button type="button" class="pick" data-key="${esc(m.key)}" style="--c:var(--${subKey(m.subscription)})" title="${esc(why)}">`
      + `<span class="pick-labels">${e.picks.map((p) => `<span class="pick-label">${esc(PICK_SHORT[p.id] || p.label)}</span>`).join('')}</span>`
      + `<span class="pick-model">${glyph(m)}<span class="pm-name">${esc(m.display_name)}</span> <span class="eff">${effLabel(v.effort)}</span></span>`
      + ['intelligence', 'cost', 'time'].map((k) => `<b class="pv">${fmt(k, v[k])}</b>`).join('')
      + '</button>';
  }).join('');
}

function renderAll() {
  renderHeader();
  renderPicks();
  renderEfforts();
  renderLineup();
  renderChanges();
  renderTradeoff();
  renderHealth();
}

/* ---------- events ---------- */
function wire() {
  $('#efforts').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-effort]');
    if (!b) return;
    state.effort = b.dataset.effort;
    clearThresholds();
    renderTradeoff();
    renderEfforts();
    renderLineup();
  });
  const lineup = $('#lineup');
  lineup.addEventListener('click', (e) => {
    const lane = e.target.closest('button[data-focus]');
    if (lane) {
      // Tap a lane header: expand it (others hidden) and sort by it. Tap again: back to all three.
      state.focus = state.focus === lane.dataset.focus ? null : lane.dataset.focus;
      state.sortBy = state.focus;
      renderLineup();
      return;
    }
    const hit = e.target.closest('.hit');
    if (hit && e.pointerType !== 'mouse') { const r = hit.getBoundingClientRect(); showTip(hit, r.left + r.width / 2, r.top + r.height / 2); e.stopPropagation(); return; }
    const row = e.target.closest('.row[data-key]');
    if (row) { hideTip(); openDetail(row.dataset.key); }
  });
  lineup.addEventListener('keydown', (e) => {
    const row = e.target.closest('.row[data-key]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openDetail(row.dataset.key); }
  });
  for (const el of [lineup, $('#tradeoff')]) {
    el.addEventListener('pointerover', (e) => { const hit = e.target.closest('.hit'); if (hit && e.pointerType === 'mouse') showTip(hit, e.clientX, e.clientY); });
    el.addEventListener('pointerout', (e) => { if (e.target.closest('.hit') && e.pointerType === 'mouse') hideTip(); });
  }
  $('#tradeoff').addEventListener('click', (e) => {
    const hit = e.target.closest('.hit');
    if (!hit) return;
    if (e.pointerType === 'mouse') { openDetail(hit.dataset.model); return; }
    const r = hit.getBoundingClientRect(); showTip(hit, r.left + r.width / 2, r.top + r.height / 2);
  });
  $('#trade-x').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-x]');
    if (b) { state.tradeX = b.dataset.x; renderTradeoff(); }
  });
  // Info button: hover or focus on desktop, tap on touch.
  const info = $('#beaten-info');
  const showInfo = () => {
    const r = info.getBoundingClientRect();
    tip.innerHTML = `<b>Beaten</b><br>${esc($('#beaten-def').textContent)}`;
    tip.hidden = false;
    const t = tip.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(r.left, innerWidth - t.width - 8)) + 'px';
    tip.style.top = (r.bottom + 6) + 'px';
  };
  info.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') showInfo(); });
  info.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') hideTip(); });
  info.addEventListener('focus', showInfo);
  info.addEventListener('blur', hideTip);
  info.addEventListener('click', (e) => { e.stopPropagation(); tip.hidden ? showInfo() : hideTip(); });
  for (const wrap of document.querySelectorAll('.thresh[data-th]')) {
    const name = wrap.dataset.th;
    const num = wrap.querySelector('.th-num');
    wrap.querySelector('.th-range').addEventListener('input', (e) => setThreshold(name, valueAt(name, Number(e.target.value))));
    num.addEventListener('input', () => { if (num.value !== '') setThreshold(name, Number(num.value)); });
    num.addEventListener('change', () => { setThreshold(name, num.value === '' ? null : Number(num.value)); num.value = state[TH[name].state] ?? ''; });
    num.addEventListener('keydown', (e) => { if (e.key === 'Escape') { setThreshold(name, null); num.value = ''; num.blur(); } });
    wrap.addEventListener('click', (e) => {
      const b = e.target.closest('button.step');
      // − / + move to the previous / next stop where the suggestion changes.
      if (b) { setThreshold(name, valueAt(name, sliderIndex(name, state[TH[name].state]) + Number(b.dataset.step))); return; }
      if (e.target.closest('button.reset')) setThreshold(name, null);
    });
  }
  $('#beaten').addEventListener('click', () => { state.showBeaten = !state.showBeaten; renderLineup(); renderTradeoff(); });
  $('#picks-plan').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-plan]');
    if (!b) return;
    state.picksPlan = b.dataset.plan;
    try { localStorage.setItem(PLAN_KEY, state.picksPlan); } catch (_) { /* no storage */ }
    renderPicks();
  });
  $('#picks').addEventListener('click', (e) => { const p = e.target.closest('.pick'); if (p) openDetail(p.dataset.key); });
  $('#more-changes').addEventListener('click', () => { state.allChanges = true; renderChanges(); });
  const dlg = $('#detail');
  $('.close', dlg).addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.hit')) hideTip(); });
  addEventListener('scroll', hideTip, { passive: true });
  let wasNarrow = innerWidth < 720;
  addEventListener('resize', () => {
    const narrow = innerWidth < 720;
    if (state.data && narrow !== wasNarrow) { wasNarrow = narrow; renderLineup(); }
  });
}

async function main() {
  wire();
  try { state.picksPlan = localStorage.getItem(PLAN_KEY) || 'overall'; } catch (_) { /* no storage */ }
  try {
    const [latest, changes] = await Promise.all([
      fetch('data/latest.json', { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
      fetch('data/changes.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : { events: [] })).catch(() => ({ events: [] })),
    ]);
    state.data = latest;
    state.events = changes.events || [];
    const present = new Set(latest.models.flatMap((m) => m.variants.map((v) => v.effort)));
    state.effort = present.has('max') ? 'max' : latest.settings.effort_order.filter((e) => present.has(e)).pop();
    clearThresholds();
    renderAll();
  } catch (err) {
    const badge = $('#badge');
    badge.className = 'badge red';
    badge.textContent = '■ No data';
    $('#lineup').innerHTML = `<p class="empty" style="padding:16px">data/latest.json didn't load (${esc(err.message)}).</p>`;
  }
}
main();
