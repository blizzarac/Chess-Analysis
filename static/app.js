/* Chess.com improvement report: single-page frontend. */
(function () {
  "use strict";
  const $ = sel => document.querySelector(sel);
  const state = { username: null, report: null, job: null, tab: "overview", puzzles: { index: 0, theme: "all", solved: {} }, gamesFilter: {}, status: null, account: null, me: null };

  // ---------- helpers -----------------------------------------------------------------------
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 1) => (v == null || Number.isNaN(v)) ? "–" : (typeof v === "number" ? v.toFixed(d) : v);
  const pct = v => v == null ? "–" : `${Number(v).toFixed(0)}%`;
  const num = v => v == null ? "–" : String(v);
  const dateOf = ts => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "–";
  const monthOf = ts => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short" }) : "–";
  const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  const evalText = cp => cp == null ? "–" : (Math.abs(cp) >= 990 ? (cp > 0 ? "+M" : "-M") : (cp / 100).toFixed(2).replace(/^(-?)/, (m, s) => s || "+"));
  const secs = s => s == null ? "–" : (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${s.toFixed(0)}s`);
  const wdl = e => Charts.wdlBar(e.wins, e.draws, e.losses).outerHTML;
  const tile = (label, value, hint, cls) => `<div class="tile ${cls || ""}"><div class="label">${esc(label)}</div><div class="value ${typeof value === "string" && /^[A-Za-z][a-z]{5,}/.test(value) ? "text" : ""}">${value}</div>${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
  const panel = (title, sub, body, extra) => `<section class="panel ${extra || ""}"><h3>${esc(title)}</h3>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}${body}</section>`;
  const scoreClass = (s, base) => s == null ? "" : (s >= (base ?? 50) + 8 ? "good" : s <= (base ?? 50) - 8 ? "bad" : "");
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || msg; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }
  function mount(html) { const c = $("#content"); c.innerHTML = html; return c; }
  const describe = (fen, uci) => ChessBoard.describeMove(fen, uci);
  const moveNo = ply => `${Math.ceil(ply / 2)}${ply % 2 ? "." : "…"}`;
  /** A small board with arrows: red = what was played, green = the engine's move, blue = also acceptable. */
  function miniBoard(ex, opts) {
    opts = opts || {};
    if (!ex || !ex.fen) return "";
    const alts = (ex.alts || []).filter(u => u !== ex.best);
    const tried = (ex.tried || []).filter(t => t.uci);
    const played = tried.length
      ? `<span class="you">You played</span> ${tried.map(t => `${esc(describe(ex.fen, t.uci))}${t.games ? ` (${t.games} game${t.games > 1 ? "s" : ""})` : ""}`).join("; ")}.`
      : (ex.uci ? `<span class="you">You played</span> ${esc(describe(ex.fen, ex.uci))}.` : "");
    const better = ex.best && ex.best !== ex.uci ? ` <span class="better">Better</span>: ${esc(describe(ex.fen, ex.best))}.` : "";
    const also = alts.length ? ` <span class="alt">Also fine</span>: ${alts.map(u => esc(describe(ex.fen, u))).join("; ")}.` : "";
    const meta = [ex.ply ? `move ${Math.ceil(ex.ply / 2)}` : "", ex.opponent ? `vs ${esc(ex.opponent)}` : "", ex.date ? dateOf(ex.date) : "", ex.win_loss != null ? `lost ${fmt(ex.win_loss, 0)}% win chance` : ""].filter(Boolean).join(" · ");
    const link = ex.game_id ? ` <a data-game="${esc(ex.game_id)}" data-ply="${ex.ply || 0}">open in the game viewer ↗</a>` : "";
    return `<div class="mini"><div class="mini-board" data-fen="${esc(ex.fen)}" data-uci="${esc(ex.uci || "")}" data-tried="${esc(tried.map(t => t.uci).join(","))}" data-best="${esc(ex.best || "")}" data-alts="${esc(alts.join(","))}" data-side="${esc(ex.side || "white")}"></div>
      <div class="cap">${opts.title ? `<b>${esc(opts.title)}</b><br>` : ""}${ex.caption ? esc(ex.caption) + " " : ""}${played}${better}${also}${opts.extra || ""}<span class="meta">${meta}${link}</span></div></div>`;
  }
  function hydrateMinis(root) {
    (root || document).querySelectorAll(".mini-board[data-fen]").forEach(el => {
      if (el.dataset.done) return;
      el.dataset.done = "1";
      const b = new ChessBoard.Board(el, { fen: el.dataset.fen, flipped: el.dataset.side === "black" });
      const marks = {}, arrows = [];
      const add = (u, color, mark) => { if (!u) return; arrows.push({ from: u.slice(0, 2), to: u.slice(2, 4), color }); if (mark) { marks[u.slice(0, 2)] = mark; marks[u.slice(2, 4)] = mark; } };
      (el.dataset.alts || "").split(",").filter(Boolean).forEach(u => add(u, "var(--info)"));
      add(el.dataset.best, "var(--good)");
      (el.dataset.tried || "").split(",").filter(Boolean).forEach((u, i) => add(u, "var(--bad)", i === 0 ? "last" : null));
      add(el.dataset.uci, "var(--bad)", "last");
      b.setPosition(el.dataset.fen, marks, arrows);
    });
    (root || document).querySelectorAll(".cap a[data-game]").forEach(a => { if (!a.dataset.done) { a.dataset.done = "1"; a.addEventListener("click", () => openGame(a.dataset.game, parseInt(a.dataset.ply, 10))); } });
  }
  const legend = () => `<div class="legend-boards"><span><span class="arrow" style="background:var(--bad)"></span>what you played</span><span><span class="arrow" style="background:var(--good)"></span>what the engine preferred</span><span><span class="arrow" style="background:var(--info)"></span>also acceptable</span><span>Boards are shown from your side of the table.</span></div>`;
  function attachChart(id, svg) { const host = document.getElementById(id); if (host) { host.innerHTML = ""; host.appendChild(svg); } }

  // ---------- theme ---------------------------------------------------------------------------
  (function themeInit() {
    let saved = null; try { saved = localStorage.getItem("theme"); } catch (e) { /* ignore */ }
    if (saved) document.documentElement.dataset.theme = saved;
    $("#theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) { /* ignore */ }
      if (state.report) renderTab();
    });
  })();

  // ---------- routing -------------------------------------------------------------------------
  const TABS = ["overview", "insights", "accuracy", "openings", "tactics", "time", "endgames", "habits", "games", "training"];
  function route() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [user, tab, sub] = hash.split("/");
    $("#account-page").hidden = true;
    if (user === "login" && tab) { verifyLogin(tab); return; }
    if (user === "account") { showAccountPage(); return; }
    if (!user) { showLanding(); return; }
    state.tab = TABS.includes(tab) ? tab : "overview";
    if (state.username !== user || !state.report) {
      loadReport(user).catch(err => { showLanding(); $("#username").value = user; alert(err.message); });
    } else {
      renderTab();
    }
    if (tab === "games" && sub) openGame(sub);
  }
  window.addEventListener("hashchange", route);

  function showLanding() {
    $("#landing").hidden = false; $("#progress").hidden = true; $("#report").hidden = true;
    state.username = null; state.report = null;
    renderLandingSide();
  }
  async function loadStatus() {
    try {
      state.status = await api("/api/status");
      state.account = state.status.account;
    } catch (e) { state.status = null; }
    renderAccountArea();
    renderCapsNote();
    if (state.status && state.status.contact_email) $("#foot-contact").innerHTML = `Questions or removal requests: <a href="mailto:${esc(state.status.contact_email)}">${esc(state.status.contact_email)}</a>`;
  }
  const capsLine = c => `${c.max_engine_games} games through the engine, depth up to ${c.max_depth}, ${c.max_months ? `${c.max_months} months of history` : "full history"}, ${c.jobs_per_day} analyses a day`;
  function renderCapsNote() {
    const st = state.status; if (!st) return;
    const c = st.caps;
    $("#max-games").max = c.max_engine_games; if (+$("#max-games").value > c.max_engine_games) $("#max-games").value = c.max_engine_games;
    document.querySelectorAll('input[name=depth]').forEach(r => { r.disabled = +r.value > c.max_depth; if (r.disabled && r.checked) document.querySelector('input[name=depth][value="10"]').checked = true; });
    if (c.max_months) $("#max-months").max = c.max_months;
    $("#caps-note").textContent = (state.account ? "Your limits: " : "Limits for visitors: ") + capsLine(c) + "." +
      (!state.account ? ` Signed-in members get ${capsLine(st.tiers.user)}.` : "");
  }
  async function renderLandingSide() {
    const side = $("#landing-side"); const st = state.status;
    const engine = st ? (st.engine.available ? `Stockfish ready (${st.engine.workers} worker${st.engine.workers > 1 ? "s" : ""}).` : "Engine not available on this server.") + (st.mock ? " Running against offline mock data." : "") : "";
    if (state.account) {
      let me = null; try { me = await api("/api/me"); state.me = me; } catch (e) { /* ignore */ }
      const players = (me && me.players) || [];
      side.innerHTML = `<h2 class="eyebrow">Your players</h2><ul class="recent-list">${players.length ? players.map(p => `<li><a href="#/${esc(p.username)}"><span>${esc(p.username)}</span><span class="num">${p.games} games · ${p.analyzed} analysed</span></a></li>`).join("") : '<li class="muted">None saved yet. Open a report and press "Save player".</li>'}</ul>
        ${me ? `<p class="muted small" style="margin-top:.8rem">${me.usage.jobs_today} of ${me.usage.jobs_per_day} analyses used today. <a href="#/account">Account</a></p>` : ""}<p class="muted small">${esc(engine)}</p>`;
    } else {
      side.innerHTML = `<h2 class="eyebrow">Free to use</h2><p class="small">Anyone can analyse a player: ${st ? capsLine(st.tiers.anonymous) : ""}.</p>
        <p class="small"><strong>Sign in</strong> (just an email, no password) to get ${st ? capsLine(st.tiers.user) : "higher limits"}, keep a list of your players, and have your puzzle progress follow you between devices.</p>
        <button type="button" class="btn primary" id="landing-signin">Sign in</button><p class="muted small" style="margin-top:1rem">${esc(engine)}</p>`;
      $("#landing-signin").addEventListener("click", openSignin);
    }
  }
  // ---------- accounts ------------------------------------------------------------------------
  function renderAccountArea() {
    const el = $("#account-area");
    if (state.account) {
      el.innerHTML = `<a href="#/account" class="email" title="${esc(state.account.email)}">${esc(state.account.email)}</a><button type="button" class="btn ghost small" id="signout">Sign out</button>`;
      $("#signout").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ } state.account = null; state.me = null; await loadStatus(); route(); });
    } else {
      el.innerHTML = `<button type="button" class="btn ghost small" id="signin-btn">Sign in</button>`;
      $("#signin-btn").addEventListener("click", openSignin);
    }
  }
  function openSignin() {
    const st = state.status;
    $("#signin-perks").innerHTML = st ? `<li>${capsLine(st.tiers.user)}</li><li>Saved players and puzzle progress on every device</li>` : "";
    $("#signin-msg").textContent = st && !st.email_enabled ? "This server has no email sending configured; the link will be shown here instead." : "";
    $("#signin").hidden = false; $("#signin-email").focus();
  }
  $("#signin-close").addEventListener("click", () => { $("#signin").hidden = true; });
  $("#signin").addEventListener("click", e => { if (e.target === $("#signin")) $("#signin").hidden = true; });
  $("#signin-form").addEventListener("submit", async e => {
    e.preventDefault();
    const msg = $("#signin-msg"); msg.textContent = "Sending…";
    try {
      const r = await api("/api/auth/request-link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: $("#signin-email").value }) });
      msg.innerHTML = r.sent ? `Check <strong>${esc(r.email)}</strong> for your sign-in link. It expires in 15 minutes.` : (r.dev_link ? `No mail server configured. <a href="${esc(r.dev_link)}">Use this link to sign in</a>.` : "The email could not be sent.");
    } catch (err) { msg.textContent = err.message; }
  });
  async function verifyLogin(token) {
    $("#landing").hidden = true; $("#report").hidden = true; $("#progress").hidden = false;
    $("#progress-title").textContent = "Signing you in…"; $("#progress-detail").textContent = ""; $("#progress-error").hidden = true;
    try {
      await api("/api/auth/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
      await loadStatus();
      $("#signin").hidden = true;
      location.hash = "#/account";
    } catch (err) {
      $("#progress-error").hidden = false; $("#progress-error").textContent = err.message;
      $("#progress-title").textContent = "Could not sign in";
    }
  }
  async function showAccountPage() {
    if (!state.account) { openSignin(); showLanding(); return; }
    $("#landing").hidden = true; $("#progress").hidden = true; $("#report").hidden = true;
    const page = $("#account-page"); page.hidden = false; page.innerHTML = "<p class='muted'>Loading…</p>";
    let me; try { me = await api("/api/me"); state.me = me; } catch (err) { page.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
    const c = me.caps, u = me.usage;
    page.innerHTML = `${head("Your account", me.account.email)}
      <div class="grid-2">
        ${panel("Limits", me.account.admin ? "Administrator account." : "What your account can run.", `<table class="data caps-table"><tbody>
          <tr><td>Games through the engine</td><td class="num">${c.max_engine_games}</td></tr><tr><td>Maximum depth</td><td class="num">${c.max_depth}</td></tr>
          <tr><td>History</td><td class="num">${c.max_months ? c.max_months + " months" : "everything"}</td></tr><tr><td>Analyses per day</td><td class="num">${u.jobs_today} / ${c.jobs_per_day}</td></tr></tbody></table>
          <div class="usage-bar"><div style="width:${Math.min(100, 100 * u.jobs_today / c.jobs_per_day)}%"></div></div>`)}
        ${panel("Your players", "Reports you have saved. Puzzle progress is kept per player.", `<ul class="recent-list" id="acct-players">${me.players.length ? me.players.map(p => `<li><a href="#/${esc(p.username)}"><span>${esc(p.username)}</span><span class="num">${p.games} games</span></a><button type="button" class="btn ghost small" data-unsave="${esc(p.username)}">remove</button></li>`).join("") : '<li class="muted">None yet. Open a report and press "Save player".</li>'}</ul>`)}
      </div>
      ${panel("Your data", "", `<p class="small">We store your email address, which players you saved, and your puzzle progress. Reports are built from public chess.com games and are shared by everyone who looks up the same player. ${state.status && state.status.contact_email ? `To delete your account or request removal of a player's report, email <a href="mailto:${esc(state.status.contact_email)}">${esc(state.status.contact_email)}</a>.` : ""}</p>`)}`;
    document.querySelectorAll("[data-unsave]").forEach(b => b.addEventListener("click", async () => { await api(`/api/me/players/${encodeURIComponent(b.dataset.unsave)}`, { method: "DELETE" }); showAccountPage(); }));
  }

  // ---------- analyze form & progress ---------------------------------------------------------
  $("#options-toggle").addEventListener("click", () => {
    const p = $("#options-panel"); p.hidden = !p.hidden; $("#options-toggle").setAttribute("aria-expanded", String(!p.hidden));
  });
  function readOptions() {
    const depth = parseInt(document.querySelector('input[name=depth]:checked').value, 10);
    const tcs = [...document.querySelectorAll('input[name=tc]:checked')].map(i => i.value);
    const mm = $("#max-months").value;
    return { depth, max_engine_games: parseInt($("#max-games").value || "100", 10), time_classes: tcs,
             max_months: mm ? parseInt(mm, 10) : null, refresh: $("#refresh").checked };
  }
  $("#analyze-form").addEventListener("submit", async e => {
    e.preventDefault();
    const username = $("#username").value.trim();
    if (!username) return;
    await startJob(username, readOptions());
  });
  $("#rerun-btn").addEventListener("click", () => state.username && startJob(state.username, readOptions()));
  $("#cancel-btn").addEventListener("click", async () => { if (state.job) await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" }); });

  async function startJob(username, options) {
    $("#landing").hidden = true; $("#report").hidden = true; $("#progress").hidden = false;
    $("#progress-error").hidden = true; $("#progress-title").textContent = `Analysing ${username}`;
    $("#analyze-btn").disabled = true;
    try {
      state.job = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, options }) });
    } catch (err) {
      $("#analyze-btn").disabled = false;
      $("#progress-error").hidden = false; $("#progress-error").textContent = err.message; return;
    }
    pollJob();
  }
  async function pollJob() {
    let job;
    try { job = await api(`/api/jobs/${state.job.id}`); } catch (e) { setTimeout(pollJob, 2000); return; }
    state.job = job;
    renderProgress(job);
    if (job.status === "done") {
      $("#analyze-btn").disabled = false;
      state.report = null; state.username = null;
      location.hash = `#/${job.username}/overview`;
      if (location.hash === `#/${job.username}/overview`) route();
      return;
    }
    if (job.status === "error" || job.status === "cancelled") {
      $("#analyze-btn").disabled = false;
      $("#progress-error").hidden = false;
      $("#progress-error").textContent = job.status === "cancelled" ? "Cancelled." : job.error;
      return;
    }
    setTimeout(pollJob, 1000);
  }
  function renderProgress(job) {
    const order = ["fetching", "analyzing", "reporting"];
    document.querySelectorAll(".stages li").forEach(li => {
      const s = li.dataset.stage;
      li.classList.toggle("active", s === job.status);
      li.classList.toggle("done", order.indexOf(s) < order.indexOf(job.status) || job.status === "done");
    });
    const p = job.progress || {};
    let frac = 0, detail = job.stage_detail || "";
    if (job.status === "queued") {
      detail = job.queue_position > 1 ? `Waiting in the queue: position ${job.queue_position}` : "Waiting for a free worker";
    } else if (job.status === "fetching") {
      const todo = (p.months_total || 0) - (p.months_cached || 0);
      frac = todo ? (p.months_done || 0) / todo * 0.25 : 0.25;
      detail += p.games_downloaded ? ` · ${p.games_downloaded} new games` : "";
    } else if (job.status === "analyzing") {
      frac = 0.25 + (p.engine_total ? (p.engine_done || 0) / p.engine_total : 1) * 0.7;
      if (p.eta_seconds != null) detail += ` · about ${secs(p.eta_seconds)} left`;
      if (p.games_total) detail += ` · ${p.games_total} games in archive`;
    } else if (job.status === "reporting") frac = 0.97;
    else if (job.status === "done") frac = 1;
    $("#progress-bar").style.width = `${Math.round(frac * 100)}%`;
    $("#progress-detail").textContent = detail;
  }

  // ---------- report loading ------------------------------------------------------------------
  async function loadReport(user) {
    const rep = await api(`/api/report/${encodeURIComponent(user)}`);
    if (state.username !== user) SRS.server = null;
    state.username = user; state.report = rep;
    $("#landing").hidden = true; $("#progress").hidden = true; $("#report").hidden = false;
    $("#username").value = user;
    renderPlayerCard();
    document.querySelectorAll("#tabs a").forEach(a => { a.href = `#/${user}/${a.dataset.tab}`; });
    renderTab();
  }
  function renderPlayerCard() {
    const r = state.report, p = r.player, ov = r.overview;
    const ratings = Object.entries(ov.by_time_class).map(([tc, e]) => `<span class="pill" title="${esc(tc)} rating">${esc(tc)} ${num(e.rating_now)}</span>`).join("");
    $("#player-card").innerHTML = `
      <div class="name">${esc(p.username)}</div>
      <div class="meta">${p.name ? esc(p.name) + " · " : ""}${p.country ? esc(p.country) + " · " : ""}${ov.games_total} games · ${ov.games_analyzed} engine-analysed</div>
      <div class="ratings">${ratings}</div>
      <div class="meta" style="margin-top:.5rem">Report built ${dateOf(r.generated_at)} · depth ${r.options.depth}</div>
      ${p.url ? `<div class="meta"><a href="${esc(p.url)}" target="_blank" rel="noopener">chess.com profile ↗</a></div>` : ""}
      ${state.account ? `<div style="margin-top:.6rem"><button type="button" class="btn small" id="save-player"></button></div>` : ""}`;
    if (state.account) {
      const btn = $("#save-player");
      const refresh = async () => { try { state.me = await api("/api/me"); } catch (e) { return; } const saved = state.me.players.some(x => x.username === r.player.username); btn.textContent = saved ? "Saved ✓ (remove)" : "Save player"; btn.dataset.saved = saved ? "1" : ""; };
      btn.addEventListener("click", async () => {
        if (btn.dataset.saved) await api(`/api/me/players/${encodeURIComponent(r.player.username)}`, { method: "DELETE" });
        else await api("/api/me/players", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: r.player.username }) });
        refresh();
      });
      refresh();
    }
  }
  function renderTab() {
    document.querySelectorAll("#tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === state.tab));
    const fn = { overview: renderOverview, insights: renderInsights, accuracy: renderAccuracy, openings: renderOpenings, tactics: renderTactics,
                 time: renderTime, endgames: renderEndgames, habits: renderHabits, games: renderGames, training: renderTraining }[state.tab];
    fn();
    hydrateMinis($("#content"));
    window.scrollTo({ top: 0 });
  }
  const head = (title, text) => `<div class="section-head"><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ""}</div>`;
  const noEngine = () => `<section class="panel"><h3>No engine analysis</h3><p class="muted">Stockfish analysis was not run for this player. Install Stockfish (set STOCKFISH_PATH) and analyse again to unlock accuracy, tactics and puzzle sections.</p></section>`;

  // ---------- overview ------------------------------------------------------------------------
  function delta(cur, prev, digits, suffix, invert) {
    if (cur == null || prev == null) return "";
    const d = cur - prev;
    if (Math.abs(d) < Math.pow(10, -(digits || 0)) / 2) return `<span class="delta flat">no change</span>`;
    const good = invert ? d < 0 : d > 0;
    return `<span class="delta ${good ? "up" : "down"}">${d > 0 ? "+" : ""}${d.toFixed(digits || 0)}${suffix || ""} since last report</span>`;
  }
  function renderOverview() {
    const r = state.report, ov = r.overview, acc = r.accuracy, prev = r.previous || null;
    const tcRows = Object.entries(ov.by_time_class).map(([tc, e]) => `
      <tr><td>${esc(cap(tc))}</td><td class="num">${e.games}</td><td>${wdl(e)}</td><td class="num">${pct(e.score)}</td>
      <td class="num">${num(e.rating_now)}</td><td class="num">${num(e.rating_peak)}</td><td class="num">${pct(e.as_white.score)}</td><td class="num">${pct(e.as_black.score)}</td></tr>`).join("");
    mount(`
      ${head("Overview", `${ov.games_total} games from ${dateOf(ov.first_game)} to ${dateOf(ov.last_game)}. ${ov.games_analyzed} of the most recent games were run through Stockfish at depth ${r.options.depth}.${ov.unknown_result_codes ? ` ${ov.unknown_result_codes} games had a result code this tool doesn't know and were counted as losses.` : ""}`)}
      <div class="tiles">
        ${tile("Games", ov.games_total, `${ov.rated} rated`)}
        ${tile("Score", pct(ov.all.score), `${ov.all.wins}W ${ov.all.draws}D ${ov.all.losses}L`, scoreClass(ov.all.score, 50))}
        ${tile("As White", pct(ov.by_color.white.score), `${ov.by_color.white.games} games`, scoreClass(ov.by_color.white.score, ov.all.score))}
        ${tile("As Black", pct(ov.by_color.black.score), `${ov.by_color.black.games} games`, scoreClass(ov.by_color.black.score, ov.all.score))}
        ${acc.available ? tile("Accuracy", fmt(acc.overall.accuracy), `avg. centipawn loss ${fmt(acc.overall.acpl, 0)}`) + "" : ""}
        ${acc.available ? tile("Blunders / game", fmt(acc.overall.blunders_per_game, 2), `${acc.overall.mistakes_per_game} mistakes`, acc.overall.blunders_per_game > 1 ? "bad" : "") : ""}
        ${tile("Longest win streak", ov.streaks.longest_win, `longest losing streak ${ov.streaks.longest_loss}`)}
        ${tile("Avg. game length", fmt(ov.avg_game_length_moves, 0), `moves${ov.puzzle_rating ? ` · puzzle rating ${ov.puzzle_rating}` : ""}`)}
      </div>
      ${prev && prev.games_total ? panel("Since your last report", `Previous report from ${dateOf(prev.generated_at)} covered ${prev.games_total} games.`, `<div class="deltas">
        <div>Games <strong>${ov.games_total - prev.games_total >= 0 ? "+" : ""}${ov.games_total - prev.games_total}</strong></div>
        <div>Score ${delta(ov.all.score, prev.score, 1, "%") || "–"}</div>
        ${acc.available ? `<div>Accuracy ${delta(acc.overall.accuracy, prev.accuracy, 1) || "–"}</div><div>Blunders / game ${delta(acc.overall.blunders_per_game, prev.blunders_per_game, 2, "", true) || "–"}</div><div>Conversion ${delta(acc.winning_positions.conversion_pct, prev.conversion_pct, 0, "%") || "–"}</div>` : ""}
        ${Object.entries(ov.by_time_class).map(([tc, e]) => prev.ratings && prev.ratings[tc] != null ? `<div>${cap(tc)} rating ${delta(e.rating_now, prev.ratings[tc], 0) || "–"}</div>` : "").join("")}
      </div>`) : ""}
      ${panel("Rating over time", "Your rating after each rated game, per time class.", `<div id="rating-chart"></div><div class="legend" id="rating-legend"></div>`)}
      <div class="grid-2">
        ${panel("Results by time class", "", `<div class="table-wrap"><table class="data"><thead><tr><th>Class</th><th class="num">Games</th><th>W / D / L</th><th class="num">Score</th><th class="num">Rating</th><th class="num">Peak</th><th class="num">White</th><th class="num">Black</th></tr></thead><tbody>${tcRows}</tbody></table></div>`)}
        ${panel("Activity", "Games per month and how you scored in them.", `<div id="activity-chart"></div>`)}
      </div>
      ${r.insights.length ? panel("Top findings", "The three things most worth working on. Full list under Insights & plan.", `<div style="display:grid;gap:.6rem">${r.insights.filter(i => i.severity !== "positive").slice(0, 3).map(insightCard).join("")}</div>`) : ""}
    `);
    // rating chart
    const series = Object.entries(r.ratings).map(([tc, pts], i) => ({ name: cap(tc), color: Charts.SERIES[i], points: pts.map(p => ({ x: p.t, y: p.rating, label: `${cap(tc)} · ${dateOf(p.t)} · ${p.rating} (${p.result})` })) }));
    attachChart("rating-chart", Charts.lineChart({ series, height: 240, xFormat: monthOf }));
    $("#rating-legend").innerHTML = series.map(s => `<span style="--swatch:${s.color}">${esc(s.name)}</span>`).join("");
    const months = r.results.by_month;
    attachChart("activity-chart", Charts.barChart({ data: months.map(m => ({ label: m.month.slice(2), value: m.games, color: m.score == null ? Charts.SERIES[0] : (m.score >= 55 ? "var(--good)" : m.score < 45 ? "var(--bad)" : "var(--ink-3)"), tip: `${m.month}: ${m.games} games, ${pct(m.score)} score` })), height: 200 }));
  }

  // ---------- insights ------------------------------------------------------------------------
  function insightCard(i) {
    return `<article class="insight ${esc(i.severity)}"><div class="sev"></div><div>
      <div class="cat"><span class="pill ${esc(i.severity)}">${esc(i.severity === "positive" ? "strength" : i.severity + " priority")}</span><span class="muted small">${esc(i.category)}</span></div>
      <h3>${esc(i.title)}</h3><div class="detail">${esc(i.detail)}</div><div class="reco">${esc(i.recommendation)}</div>${i.example ? miniBoard(i.example, { title: "For example" }) : ""}</div></article>`;
  }
  function renderInsights() {
    const r = state.report;
    const plan = r.training_plan.map(p => `<div class="plan-item"><div><div class="eyebrow">${esc(p.category)}</div><h3>${esc(p.focus)}</h3><p class="muted">${esc(p.how)}</p><ul>${p.drills.map(d => `<li>${esc(d)}</li>`).join("")}</ul></div></div>`).join("");
    mount(`
      ${head("Insights & plan", "Findings ranked by how many rating points they are likely costing you. Each needs a minimum sample before it is shown, so a short history produces fewer findings. Where a finding has a concrete example from your games, the position is shown.")}
      ${legend()}
      ${r.training_plan.length ? `<section class="panel"><h3>Your training plan</h3><div class="sub">One focus per category, in priority order.</div><div class="plan">${plan}</div></section>` : ""}
      <div style="display:grid;gap:.8rem">${r.insights.length ? r.insights.map(insightCard).join("") : `<section class="panel"><p class="muted">Not enough games yet for confident findings. Play more, or lower the engine depth and analyse more games.</p></section>`}</div>`);
  }

  // ---------- accuracy ------------------------------------------------------------------------
  function renderAccuracy() {
    const r = state.report, a = r.accuracy;
    if (!a.available) { mount(head("Accuracy") + noEngine()); return; }
    const phaseRows = ["opening", "middlegame", "endgame"].map(p => { const e = a.by_phase[p]; return `<tr><td>${cap(p)}</td><td class="num">${e.moves}</td><td class="num">${fmt(e.acpl)}</td><td class="num">${e.blunders}</td><td class="num">${e.mistakes}</td><td class="num">${fmt(e.blunder_rate_per_100, 2)}</td></tr>`; }).join("");
    const grp = (label, e) => `<tr><td>${esc(label)}</td><td class="num">${e.games}</td><td class="num">${fmt(e.accuracy)}</td><td class="num">${fmt(e.acpl)}</td><td class="num">${fmt(e.blunders_per_game, 2)}</td></tr>`;
    const groups = [["As White", a.by_color.white], ["As Black", a.by_color.black], ...Object.entries(a.by_time_class).map(([k, v]) => [cap(k), v]),
                    ["In wins", a.by_result.win], ["In draws", a.by_result.draw], ["In losses", a.by_result.loss], ["Your opponents", a.opponents]];
    mount(`
      ${head("Accuracy", "How closely your moves match Stockfish. Accuracy follows the Lichess formula (100 = engine-perfect); centipawn loss is how much evaluation each move gives away on average.")}
      <div class="tiles">
        ${tile("Accuracy", fmt(a.overall.accuracy), `${a.overall.games} games analysed`)}
        ${tile("Centipawn loss", fmt(a.overall.acpl, 0), "average per move")}
        ${tile("Blunders / game", fmt(a.overall.blunders_per_game, 2), "", a.overall.blunders_per_game > 1 ? "bad" : "")}
        ${tile("Winning positions converted", pct(a.winning_positions.conversion_pct), `${a.winning_positions.games - a.winning_positions.not_won} of ${a.winning_positions.games} games at +3`, a.winning_positions.conversion_pct < 70 ? "bad" : "good")}
        ${tile("Lost positions saved", pct(a.losing_positions.save_pct), `${a.losing_positions.saved} of ${a.losing_positions.games} games at -3`)}
        ${tile("Premature resignations", a.premature_resignations, "resigned within 1.5 pawns of equal", a.premature_resignations >= 3 ? "warn" : "")}
      </div>
      <div class="grid-2">
        ${panel("Move quality", `${a.total_moves} of your moves, classified by win-probability lost.`, `<div id="class-chart"></div>`)}
        ${panel("By phase", "Where the centipawns go. The blunder rate is per 100 moves in that phase.", `<div class="table-wrap"><table class="data"><thead><tr><th>Phase</th><th class="num">Moves</th><th class="num">CP loss</th><th class="num">Blunders</th><th class="num">Mistakes</th><th class="num">Blunders / 100</th></tr></thead><tbody>${phaseRows}</tbody></table></div>`)}
      </div>
      ${panel("Centipawn loss by move number", "Spikes show where in a game you typically go wrong.", `<div id="move-chart"></div>`)}
      <div class="grid-2">
        ${panel("Accuracy over time", "Monthly average.", `<div id="trend-chart"></div>`)}
        ${panel("Breakdown", "", `<div class="table-wrap"><table class="data"><thead><tr><th>Group</th><th class="num">Games</th><th class="num">Accuracy</th><th class="num">CP loss</th><th class="num">Blunders / game</th></tr></thead><tbody>${groups.filter(g => g[1].games).map(g => grp(g[0], g[1])).join("")}</tbody></table></div>`)}
      </div>`);
    const cc = a.class_counts;
    attachChart("class-chart", Charts.barChart({ data: ["best", "excellent", "good", "inaccuracy", "mistake", "blunder"].map(c => ({ label: cap(c), value: cc[c] || 0, color: `var(--c-${c})`, tip: `${cap(c)}: ${cc[c] || 0} (${(100 * (cc[c] || 0) / a.total_moves).toFixed(1)}%)` })), valueLabels: true, height: 200 }));
    attachChart("move-chart", Charts.lineChart({ series: [{ name: "CP loss", points: a.by_move_number.map(m => ({ x: m.move, y: m.avg_cp_loss, label: `Move ${m.move}: ${m.avg_cp_loss} cp (${m.n} moves)` })) }], height: 200, area: true, xFormat: x => `move ${Math.round(x)}`, yMin: 0 }));
    attachChart("trend-chart", Charts.lineChart({ series: [{ name: "Accuracy", points: a.trend.map((t, i) => ({ x: i, y: t.accuracy, label: `${t.month}: ${t.accuracy} (${t.games} games)` })) }], height: 200, xFormat: x => (a.trend[Math.round(x)] || {}).month || "", yMin: 40, yMax: 100 }));
  }

  // ---------- openings ------------------------------------------------------------------------
  function treeHtml(node, depth) {
    if (!node.children || !node.children.length) return "";
    return node.children.map(ch => {
      const leaf = !ch.children.length;
      const mvno = Math.ceil(ch.ply / 2) + (ch.ply % 2 ? ". " : "… ");
      return `<details class="${leaf ? "leaf" : ""}" ${depth < 1 ? "open" : ""}><summary data-fen="${esc(ch.fen || "")}" data-uci="${esc(ch.uci || "")}" data-fen-before="${esc(ch.fen_before || "")}" data-ply="${ch.ply}"><span class="san">${mvno}${esc(ch.san)}</span><span class="n">${ch.games}</span>${Charts.wdlBar(ch.wins, ch.draws, ch.losses).outerHTML}<span class="score ${scoreClass(ch.score, 50)}">${pct(ch.score)}</span></summary>${leaf ? "" : treeHtml(ch, depth + 1)}</details>`;
    }).join("");
  }
  function renderOpenings() {
    const r = state.report, op = r.openings, base = r.overview.all.score;
    const mistakes = o => (o.typical_mistakes || []).map(m => `<div class="small">Move ${Math.ceil(m.ply / 2)}: ${esc(m.fen ? describe(m.fen, m.uci) : m.san)} <span class="cls ${m.class}">${m.class}</span>, ${m.games} game${m.games > 1 ? "s" : ""}</div>`).join("") || '<span class="muted small">–</span>';
    const mistakeBoards = color => {
      const rows = op[color].openings.filter(o => (o.typical_mistakes || []).length && o.typical_mistakes[0].fen).slice(0, 4);
      if (!rows.length) return "";
      return panel(`Your usual first slip as ${cap(color)}`, "The move where you most often first leave the engine's approval in your most played openings.", rows.map(o => miniBoard({ ...o.typical_mistakes[0], side: color, caption: `${o.name}: seen in ${o.typical_mistakes[0].games} game${o.typical_mistakes[0].games > 1 ? "s" : ""}.` }, { title: o.name })).join(""));
    };
    const table = (rows, color) => `<div class="table-wrap"><table class="data"><thead><tr><th>Opening</th><th class="num">Games</th><th>W / D / L</th><th class="num">Score</th><th class="num">Accuracy</th><th class="num">Eval after move 10</th><th>Where you first go wrong</th></tr></thead><tbody>
      ${rows.map(o => `<tr class="clickable" data-open="${esc(o.example_ids[0] || "")}" data-name="${esc(o.name)}" data-color="${color}" title="${o.deep_dive ? "Show where you go wrong in this opening" : "Open the most recent game"}"><td>${esc(o.name)}${o.eco ? ` <span class="muted small">${esc(o.eco)}</span>` : ""}</td><td class="num">${o.games}</td><td>${wdl(o)}</td><td class="num ${scoreClass(o.score, base)}">${pct(o.score)}</td><td class="num">${fmt(o.accuracy)}</td><td class="num">${o.avg_eval_after_opening == null ? "–" : evalText(o.avg_eval_after_opening)}</td><td>${mistakes(o)}</td></tr>`).join("")}</tbody></table></div>`;
    const devs = color => {
      const d = op[color].deviations || [], lb = op[color].left_book_first || {};
      const intro = `<div class="sub">Against a compact book of ${"~200"} standard lines. You left the book first in ${lb.player || 0} games, your opponents in ${lb.opponent || 0}.</div>`;
      if (!d.length) return intro + '<span class="muted small">No departures recorded.</span>';
      return intro + d.slice(0, 4).map(x => miniBoard({ fen: x.fen, uci: x.uci, best: (x.book_ucis || [])[0], alts: (x.book_ucis || []).slice(1), side: color, ply: x.ply,
        caption: `${x.games} game${x.games > 1 ? "s" : ""}, ${pct(x.score)} score.` }, { title: `Move ${Math.ceil(x.ply / 2)}: you leave the book here` })).join("");
    };
    const side = color => `<div class="grid-32">
      ${panel(`As ${cap(color)}`, `${op[color].games} games, ${op[color].distinct_openings} distinct openings (chess.com's classification). Score is coloured against your overall ${pct(base)}. Eval is from your side after 10 moves.`, table(op[color].openings.slice(0, 15), color))}
      <div class="stack">${panel(`${cap(color)} repertoire map`, "The first five moves of each game, with how often each branch occurs and how it scores. Click a move to see the position.", `<div class="tree-with-board"><div class="tree" data-color="${color}">${treeHtml(op[color].tree, 0) || '<span class="muted">No games.</span>'}</div><div class="mini" id="tree-board-${color}"><div class="mini-board" data-fen="${esc(op[color].tree.fen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")}" data-side="${color}"></div><div class="cap muted small">Starting position</div></div></div>`)}
      ${panel(`Where you leave theory as ${cap(color)}`, "", devs(color))}</div></div>
      ${mistakeBoards(color)}`;
    const dives = [];
    for (const color of ["white", "black"]) for (const o of op[color].openings) if (o.deep_dive) dives.push({ color, o });
    dives.sort((a, b) => b.o.deep_dive.win_loss_per_game * Math.min(b.o.deep_dive.analyzed, 5) - a.o.deep_dive.win_loss_per_game * Math.min(a.o.deep_dive.analyzed, 5));
    const diveOptions = dives.map((d, i) => `<option value="${i}">as ${cap(d.color)} · ${esc(d.o.name)} (${d.o.deep_dive.analyzed} analysed, ${fmt(d.o.deep_dive.errors_per_game, 1)} errors per game)</option>`).join("");
    mount(`${head("Openings", "What you actually play, and what it does to your results. Look for lines with many games and a low score: that is where a few hours of study pay off fastest.")}
      ${legend()}
      ${dives.length ? `<section class="panel" id="dive"><h3>Where you go wrong in a specific opening</h3><div class="sub">Every engine-flagged error you made in games with this opening, at any point of the game, grouped by the position it happened in. Pick an opening or click a row in the tables below.</div>
        <select id="dive-select" style="max-width:100%;padding:.4rem .6rem;border:1px solid var(--line-strong);border-radius:6px;background:var(--surface);color:var(--ink)">${diveOptions}</select>
        <div id="dive-body" style="margin-top:1rem"></div></section>` : ""}
      ${side("white")}${side("black")}`);
    const renderDive = i => {
      const d = dives[i]; if (!d) return;
      const o = d.o, dd = o.deep_dive, color = d.color;
      const spots = dd.trouble_spots.map((t, k) => miniBoard({ fen: t.fen, tried: t.tried, best: t.best, side: color, ply: t.ply, game_id: t.example_game_id,
        caption: `${cap(t.phase)}, before move ${t.move}, ${t.repeated ? `reached in ${t.games} games` : "seen once"}; ${t.repeated ? "on average " : ""}you lose ${fmt(t.avg_win_loss, 0)}% win chance here. Results from here: ${Object.entries(t.results).map(([r, n]) => `${n} ${r}${n > 1 && r !== "loss" ? "s" : n > 1 ? "es" : ""}`).join(", ")}.` },
        { title: t.repeated ? `Trouble spot ${k + 1}` : `Costly moment ${k + 1}` })).join("");
      $("#dive-body").innerHTML = `
        <p style="font-size:1.05rem;max-width:80ch"><strong>${esc(o.name)} as ${cap(color)}.</strong> ${esc(dd.summary)}</p>
        <div class="tiles">
          ${tile("Games", o.games, `${dd.analyzed} engine-analysed`)}
          ${tile("Score", pct(o.score), `${o.wins}W ${o.draws}D ${o.losses}L`, scoreClass(o.score, base))}
          ${tile("Errors per game", fmt(dd.errors_per_game, 1), `each costing ${fmt(dd.avg_loss_per_error, 0)}% win chance on average`, dd.errors_per_game >= 3 ? "bad" : "")}
          ${tile("Where the damage is", cap(Object.entries(dd.win_loss_by_phase).sort((a, b) => b[1] - a[1])[0][0]), `opening ${fmt(dd.win_loss_by_phase.opening, 0)} · middlegame ${fmt(dd.win_loss_by_phase.middlegame, 0)} · endgame ${fmt(dd.win_loss_by_phase.endgame, 0)} (win chance points per game)`)}
          ${tile("First slip", dd.avg_first_error_move ? `move ${dd.avg_first_error_move}` : "–", "on average")}
          ${tile("Turns against you", dd.turning_move ? `move ${dd.turning_move}` : (dd.worst_drop_move ? `move ${dd.worst_drop_move}` : "–"), dd.turning_move ? "average eval below −0.6" : dd.worst_drop_move ? "sharpest average drop" : "no typical collapse")}
        </div>
        <div class="grid-2" style="margin-top:1rem">
          ${panel("Average evaluation by move", "From your side, averaged over the analysed games in this opening. Below zero means you are typically worse.", `<div id="dive-curve"></div>`)}
          ${panel("Trouble spots", dd.trouble_spots.length ? (dd.trouble_spots.some(t => t.repeated) ? "Positions you reach repeatedly and get wrong. Red arrows are the moves you tried, green is the engine's choice." : "You rarely reach the same position twice in this line, so these are the single costliest moments.") : "The engine found no errors in this line.", spots || "")}
        </div>`;
      attachChart("dive-curve", Charts.lineChart({ series: [{ name: "Eval", points: dd.eval_curve.map(c => ({ x: c.move, y: c.avg_eval / 100, label: `After move ${c.move}: ${(c.avg_eval / 100).toFixed(2)} (${c.n} games)` })) }], height: 200, area: true, refY: 0, xFormat: x => `move ${Math.round(x)}`, yFormat: y => y.toFixed(1) }));
      hydrateMinis($("#dive-body"));
    };
    if (dives.length) {
      $("#dive-select").addEventListener("change", e => renderDive(+e.target.value));
      renderDive(0);
    }
    document.querySelectorAll("tr[data-open]").forEach(tr => tr.addEventListener("click", () => {
      const i = dives.findIndex(d => d.o.name === tr.dataset.name && d.color === tr.dataset.color);
      if (i >= 0) { $("#dive-select").value = String(i); renderDive(i); $("#dive").scrollIntoView({ behavior: "smooth", block: "start" }); }
      else if (tr.dataset.open) openGame(tr.dataset.open);
    }));
    mount.treeBoards = {};
    document.querySelectorAll(".tree summary[data-fen]").forEach(sm => sm.addEventListener("click", e => {
      const color = sm.closest(".tree").dataset.color;
      const host = document.querySelector(`#tree-board-${color} .mini-board`);
      const cap = document.querySelector(`#tree-board-${color} .cap`);
      if (!host || !sm.dataset.fen) return;
      let b = mount.treeBoards[color];
      if (!b) { host.innerHTML = ""; delete host.dataset.done; b = mount.treeBoards[color] = new ChessBoard.Board(host, { fen: sm.dataset.fen, flipped: color === "black" }); }
      const u = sm.dataset.uci;
      b.setPosition(sm.dataset.fen, u ? { [u.slice(0, 2)]: "last", [u.slice(2, 4)]: "last" } : {}, []);
      cap.innerHTML = `After ${moveNo(parseInt(sm.dataset.ply, 10))} <b>${esc(sm.querySelector(".san").textContent.replace(/^[\d.… ]+/, ""))}</b>: ${esc(describe(sm.dataset.fenBefore, u))}.`;
      document.querySelectorAll(".tree summary.cur").forEach(x => x.classList.remove("cur")); sm.classList.add("cur");
    }));
  }

  // ---------- tactics -------------------------------------------------------------------------
  function renderTactics() {
    const r = state.report, t = r.tactics;
    if (!t.available) { mount(head("Tactics") + noEngine()); return; }
    const ex = Object.entries(t.examples).map(([tag, list]) => {
      const label = (t.tag_counts.find(x => x.tag === tag) || {}).label || tag;
      return `<section class="panel"><h3>${esc(label)}</h3>${list.slice(0, 2).map(e => miniBoard(e)).join("")}</section>`;
    }).join("");
    mount(`${head("Tactics", "Why your bad moves were bad. Every mistake and blunder in the analysed games was checked for what happened next: a hanging piece, a missed mate, a fork. Each cause below comes with positions from your games.")}
      ${legend()}
      <div class="tiles">
        ${tile("Opponent blunders punished", pct(t.opponent_blunders.punish_pct), `${t.opponent_blunders.punished} of ${t.opponent_blunders.count} chances`, t.opponent_blunders.punish_pct != null && t.opponent_blunders.punish_pct < 60 ? "bad" : "good")}
        ${tile("Forced mates missed", t.mates_missed, "", t.mates_missed ? "warn" : "")}
        ${tile("Pieces hung", Object.values(t.pieces_hung).reduce((a, b) => a + b, 0), Object.entries(t.pieces_hung).map(([k, v]) => `${k}:${v}`).join(" "))}
      </div>
      ${panel("What went wrong", "Count of your mistakes and blunders by cause, across analysed games. One move can carry several causes.", `<div id="tag-chart"></div>`)}
      <div class="mini-grid">${ex}</div>`);
    attachChart("tag-chart", Charts.hbarChart({ data: t.tag_counts.map(x => ({ label: x.label, value: x.count, extra: `${x.count} · ${x.games_pct}% of games`, tip: `${x.label}: ${x.count} moves in ${x.games} games` })), labelWidth: 230, extraWidth: 150 }));
  }

  // ---------- time ----------------------------------------------------------------------------
  function renderTime() {
    const r = state.report, t = r.time;
    if (!t.available) { mount(head("Clock") + `<section class="panel"><p class="muted">No clock information in these games (daily games, or PGNs without clock tags).</p></section>`); return; }
    const rows = Object.entries(t.by_time_class).map(([tc, e]) => `<tr><td>${cap(tc)}</td><td class="num">${e.games}</td><td class="num">${pct(e.pct_clock_used_by_move_10)}</td><td class="num">${secs(e.avg_time_by_phase.opening)}</td><td class="num">${secs(e.avg_time_by_phase.middlegame)}</td><td class="num">${secs(e.avg_time_by_phase.endgame)}</td><td class="num">${e.time_trouble_games}</td><td class="num">${e.timeouts}</td><td class="num">${e.won_on_time}</td><td class="num">${secs(e.avg_clock_left_at_end)}</td></tr>`).join("");
    const er = t.error_rate;
    mount(`${head("Clock", "Where your time goes and what it costs. Time trouble means under 10% of the starting clock (5% with increment), at least 5 seconds.")}
      <div class="tiles">
        ${tile("Error rate in time trouble", pct(er.time_trouble.rate), `${er.time_trouble.errors} errors in ${er.time_trouble.moves} moves`, er.time_trouble.rate > (er.normal.rate || 0) * 1.5 ? "bad" : "")}
        ${tile("Error rate otherwise", pct(er.normal.rate), `${er.normal.errors} errors in ${er.normal.moves} moves`)}
        ${tile("Timeouts", Object.values(t.by_time_class).reduce((a, e) => a + e.timeouts, 0), "games lost on time", "warn")}
        ${tile("Won on time", Object.values(t.by_time_class).reduce((a, e) => a + e.won_on_time, 0), "opponent flagged")}
      </div>
      ${panel("Time use by time class", "Average time spent per phase; time-trouble games are those where you dipped under the threshold.", `<div class="table-wrap"><table class="data"><thead><tr><th>Class</th><th class="num">Games</th><th class="num">Clock used by move 10</th><th class="num">Opening</th><th class="num">Middlegame</th><th class="num">Endgame</th><th class="num">Time-trouble games</th><th class="num">Timeouts</th><th class="num">Won on time</th><th class="num">Left at end</th></tr></thead><tbody>${rows}</tbody></table></div>`)}
      ${panel("Move quality by thinking time", "Average centipawn loss of moves after move 10 (blitz and slower), grouped by how long you thought.", `<div id="think-chart"></div>`)}`);
    attachChart("think-chart", Charts.barChart({ data: t.cp_loss_by_think_time.map(b => ({ label: b.bucket, value: b.avg_cp_loss, tip: `${b.bucket}: ${b.avg_cp_loss} cp over ${b.n} moves` })), valueLabels: true, height: 200 }));
  }

  // ---------- endgames ------------------------------------------------------------------------
  function renderEndgames() {
    const r = state.report, e = r.endgames;
    if (!e.available) { mount(head("Endgames") + `<section class="panel"><p class="muted">No games.</p></section>`); return; }
    const c = e.conversion;
    const rows = e.by_type.map(t => `<tr><td>${cap(t.type)}</td><td class="num">${t.games}</td><td>${wdl(t)}</td><td class="num">${pct(t.score)}</td><td class="num">${t.winning_endgames ? `${t.converted}/${t.winning_endgames} (${pct(t.conversion_pct)})` : "–"}</td><td class="num">${t.balanced_endgames ? pct(t.held_pct) : "–"}</td><td class="num">${fmt(t.acpl)}</td></tr>`).join("");
    mount(`${head("Endgames", "An endgame starts when six or fewer pieces (excluding kings and pawns) remain. Conversion counts endgames entered at +2 or better; holding counts endgames that started level.")}
      <div class="tiles">
        ${tile("Games reaching an endgame", pct(e.reach_pct), `${e.games_reaching_endgame} games`)}
        ${tile("Score in endgames", pct(e.results_in_endgames.score), `${e.results_in_endgames.wins}W ${e.results_in_endgames.draws}D ${e.results_in_endgames.losses}L`, scoreClass(e.results_in_endgames.score, r.overview.all.score))}
        ${tile("Winning endgames converted", pct(c.winning.pct), `${c.winning.won} of ${c.winning.games}`, c.winning.pct != null && c.winning.pct < 70 ? "bad" : "good")}
        ${tile("Level endgames held", pct(c.balanced.hold_pct), `${c.balanced.won} won, ${c.balanced.games - c.balanced.not_lost} lost of ${c.balanced.games}`)}
        ${tile("Lost endgames saved", pct(c.losing.save_pct), `${c.losing.saved} of ${c.losing.games}`)}
        ${tile("Endgame CP loss", fmt(e.acpl_endgame, 0), "average per move")}
      </div>
      ${panel("By endgame type", "Named by the heaviest material left when the endgame began.", `<div class="table-wrap"><table class="data"><thead><tr><th>Type</th><th class="num">Games</th><th>W / D / L</th><th class="num">Score</th><th class="num">Converted</th><th class="num">Held</th><th class="num">CP loss</th></tr></thead><tbody>${rows}</tbody></table></div>`)}`);
  }

  // ---------- habits --------------------------------------------------------------------------
  function renderHabits() {
    const r = state.report, res = r.results, base = r.overview.all.score;
    const term = (obj) => Object.entries(obj).map(([k, v]) => ({ label: k, value: v }));
    const tilt = res.tilt;
    mount(`${head("Habits", "When you play, how you react to losses, and whom you struggle against. Hours are in UTC.")}
      <div class="tiles">
        ${tile("Score after a loss", pct(tilt.after_loss.score), `${tilt.after_loss.games} games started within 20 min of a loss`, tilt.after_loss.score != null && tilt.after_loss.score < (tilt.baseline.score || 0) - 8 ? "bad" : "")}
        ${tile("Score otherwise", pct(tilt.baseline.score), `${tilt.baseline.games} games`)}
        ${tile("Rematches after a loss", pct(tilt.rematch_after_loss.score), `${tilt.rematch_after_loss.games} games`)}
      </div>
      <div class="grid-2x">
        ${panel("Score by hour of day (UTC)", "Bars are score; faint bars have fewer than 8 games.", `<div id="hour-chart"></div>`)}
        ${panel("Score by weekday", "", `<div id="weekday-chart"></div>`)}
        ${panel("Score by game number within a session", "A session is a run of games with less than 30 minutes between them.", `<div id="session-chart"></div>`)}
        ${panel("Score against rating difference", "How you do against weaker and stronger opponents.", `<div id="vs-chart"></div>`)}
        ${panel("How you win", "", `<div id="win-chart"></div>`)}
        ${panel("How you lose", "", `<div id="loss-chart"></div>`)}
      </div>`);
    const dim = e => e.games < 8 ? "var(--line-strong)" : (e.score >= base + 8 ? "var(--good)" : e.score <= base - 8 ? "var(--bad)" : Charts.SERIES[0]);
    attachChart("hour-chart", Charts.barChart({ data: res.by_hour.map(h => ({ label: h.hour, value: h.games ? h.score : null, color: dim(h), tip: `${String(h.hour).padStart(2, "0")}:00 · ${h.games} games · ${pct(h.score)}` })), yMax: 100, refY: base, height: 200 }));
    attachChart("weekday-chart", Charts.barChart({ data: res.by_weekday.map(h => ({ label: h.weekday, value: h.games ? h.score : null, color: dim(h), tip: `${h.weekday} · ${h.games} games · ${pct(h.score)}` })), yMax: 100, refY: base, height: 200 }));
    attachChart("session-chart", Charts.barChart({ data: res.session_curve.map(h => ({ label: h.game_no, value: h.games ? h.score : null, color: dim(h), tip: `Game ${h.game_no} of a session · ${h.games} games · ${pct(h.score)}` })), yMax: 100, refY: base, height: 200 }));
    attachChart("vs-chart", Charts.barChart({ data: res.vs_rating.map(h => ({ label: h.bucket.replace(" higher", "↑").replace(" lower", "↓"), value: h.games ? h.score : null, color: dim(h), tip: `Opponent ${h.bucket} · ${h.games} games · ${pct(h.score)}` })), yMax: 100, refY: base, height: 200 }));
    attachChart("win-chart", Charts.hbarChart({ data: term(res.termination_wins).map(x => ({ ...x, color: "var(--good)" })), labelWidth: 200 }));
    attachChart("loss-chart", Charts.hbarChart({ data: term(res.termination_losses).map(x => ({ ...x, color: "var(--bad)" })), labelWidth: 200 }));
  }

  // ---------- games list ----------------------------------------------------------------------
  function renderGames() {
    const r = state.report;
    const f = state.gamesFilter;
    const tcs = Object.keys(r.overview.by_time_class);
    mount(`${head("Games", `${r.overview.games_total} games. Click a row to open the analysis board.`)}
      <section class="panel">
        <div class="filters">
          <select id="f-tc"><option value="">All time classes</option>${tcs.map(t => `<option ${f.tc === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
          <select id="f-result"><option value="">All results</option>${["win", "draw", "loss"].map(t => `<option ${f.result === t ? "selected" : ""}>${t}</option>`).join("")}</select>
          <select id="f-color"><option value="">Both colours</option>${["white", "black"].map(t => `<option ${f.color === t ? "selected" : ""}>${t}</option>`).join("")}</select>
          <label><input type="checkbox" id="f-analyzed" ${f.analyzed ? "checked" : ""}> analysed only</label>
          <input id="f-search" type="search" placeholder="opponent or opening" value="${esc(f.q || "")}">
        </div>
        <div class="table-wrap"><table class="data" id="games-table"><thead><tr><th>Date</th><th>Class</th><th>Colour</th><th>Opponent</th><th>Result</th><th>Opening</th><th class="num">Moves</th><th class="num">Accuracy</th><th class="num">Blunders</th></tr></thead><tbody></tbody></table></div>
        <div class="pager"><button class="btn small" id="pg-prev">‹ Prev</button><span id="pg-info" class="muted small"></span><button class="btn small" id="pg-next">Next ›</button></div>
      </section>`);
    const PAGE = 50;
    let page = 0, timer = null;
    const draw = async () => {
      f.tc = $("#f-tc").value; f.result = $("#f-result").value; f.color = $("#f-color").value; f.analyzed = $("#f-analyzed").checked; f.q = $("#f-search").value;
      const params = new URLSearchParams({ offset: page * PAGE, limit: PAGE });
      if (f.tc) params.set("time_class", f.tc); if (f.result) params.set("result", f.result); if (f.color) params.set("color", f.color);
      if (f.analyzed) params.set("analyzed", "true"); if (f.q) params.set("q", f.q);
      let data;
      try { data = await api(`/api/players/${encodeURIComponent(state.username)}/games?${params}`); } catch (err) { $("#pg-info").textContent = err.message; return; }
      const pages = Math.max(1, Math.ceil(data.total / PAGE));
      $("#games-table tbody").innerHTML = data.games.map(g => `<tr class="clickable" data-id="${esc(g.id)}">
        <td class="mono small">${dateOf(g.date)}</td><td>${esc(g.time_class)} <span class="muted small">${esc(g.time_control)}</span></td><td>${g.color === "white" ? "♔ White" : "♚ Black"}</td>
        <td>${esc(g.opponent)} <span class="muted small">${num(g.opponent_rating)}</span></td><td><span class="pill ${g.result}">${g.result}</span> <span class="muted small">${esc(g.termination)}</span></td>
        <td class="small">${esc(g.opening || "")}</td><td class="num">${g.moves}</td><td class="num">${g.analyzed ? fmt(g.accuracy) : '<span class="muted">–</span>'}</td><td class="num">${g.analyzed ? g.blunders : "–"}</td></tr>`).join("") || '<tr><td colspan="9" class="muted">No games match.</td></tr>';
      $("#pg-info").textContent = `${data.total} games · page ${page + 1} of ${pages}`;
      $("#pg-prev").disabled = page === 0; $("#pg-next").disabled = page >= pages - 1;
      document.querySelectorAll("#games-table tr[data-id]").forEach(tr => tr.addEventListener("click", () => openGame(tr.dataset.id)));
    };
    ["#f-tc", "#f-result", "#f-color", "#f-analyzed"].forEach(s => $(s).addEventListener("change", () => { page = 0; draw(); }));
    $("#f-search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { page = 0; draw(); }, 250); });
    $("#pg-prev").addEventListener("click", () => { page--; draw(); });
    $("#pg-next").addEventListener("click", () => { page++; draw(); });
    draw();
  }

  // ---------- game viewer ---------------------------------------------------------------------
  const viewer = { data: null, ply: 0, board: null, evalSvg: null };
  $("#viewer-close").addEventListener("click", closeViewer);
  $("#viewer").addEventListener("click", e => { if (e.target === $("#viewer")) closeViewer(); });
  document.addEventListener("keydown", e => {
    if ($("#viewer").hidden) return;
    if (e.key === "Escape") closeViewer();
    if (e.key === "ArrowLeft") { e.preventDefault(); gotoPly(viewer.ply - 1); }
    if (e.key === "ArrowRight") { e.preventDefault(); gotoPly(viewer.ply + 1); }
    if (e.key === "Home") gotoPly(0);
    if (e.key === "End") gotoPly(viewer.data.analysis.moves.length);
  });
  function closeViewer() { $("#viewer").hidden = true; viewer.data = null; }
  async function openGame(id, ply) {
    if (!state.username) return;
    let data;
    try { data = await api(`/api/games/${encodeURIComponent(state.username)}/${encodeURIComponent(id)}`); } catch (err) { alert(err.message); return; }
    viewer.data = data;
    const g = data.game, an = data.analysis;
    $("#viewer-title").textContent = `${g.white} (${num(g.white_rating)}) vs ${g.black} (${num(g.black_rating)})`;
    const me = an[g.player_color], opp = an[g.player_color === "white" ? "black" : "white"];
    const movesHtml = [];
    for (let i = 0; i < an.moves.length; i += 2) {
      const w = an.moves[i], b = an.moves[i + 1];
      const cell = m => m ? `<span class="mv" data-ply="${m.ply}"><span><span class="cls ${m.class || ""}">${esc(m.san)}</span></span><span class="ev">${m.eval_text || ""}</span></span>` : "<span></span>";
      movesHtml.push(`<div class="row"><span class="no">${i / 2 + 1}.</span>${cell(w)}${cell(b)}</div>`);
    }
    const crit = (an.critical_moments || []).map(c => `<li data-ply="${c.ply}"><span class="cls ${c.class}">${c.class}</span><span>Move ${Math.ceil(c.ply / 2)}, ${c.color === g.player_color ? "you" : "opponent"}: ${esc(c.fen ? describe(c.fen, c.uci) : c.san)}${c.best && c.best !== c.uci && c.fen ? `; better was ${esc(describe(c.fen, c.best))}` : ""}</span><span class="muted small">−${fmt(c.win_loss, 0)}%</span></li>`).join("");
    $("#viewer-body").innerHTML = `
      <div class="game-meta"><span>${dateOf(g.end_time)}</span><span>${esc(g.time_class)} ${esc(g.time_control)}</span><span>${esc(g.opening_name || "")}${g.eco ? ` (${esc(g.eco)})` : ""}</span>
        <span><span class="pill ${g.player_result}">${g.player_result}</span> ${esc(g.termination)}</span>${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">chess.com ↗</a>` : ""}</div>
      <div class="game-layout">
        <div>
          <div class="board-col"><div class="evalbar" id="evalbar"><div class="white"></div><div class="txt"></div></div><div class="board-wrap"><div id="board"></div></div></div>
          <div class="board-controls">
            <button class="btn small" id="b-start">⏮</button><button class="btn small" id="b-prev">‹</button><button class="btn small" id="b-next">›</button><button class="btn small" id="b-end">⏭</button>
            <button class="btn small" id="b-flip">Flip</button><span class="spacer"></span><span class="muted small">← → keys</span>
          </div>
          <div id="eval-chart" style="margin-top:.6rem"></div>
          <div class="move-info" id="move-info"></div>
          <div style="margin-top:.5rem">${legend()}</div>
        </div>
        <div>
          ${an.engine ? `<div class="tiles" style="margin-bottom:.8rem">${tile(`${cap(g.player_color)} (you)`, fmt(me.accuracy), `cp loss ${fmt(me.acpl, 0)} · ${me.classes.blunder} blunders, ${me.classes.mistake} mistakes`)}${tile("Opponent", fmt(opp.accuracy), `cp loss ${fmt(opp.acpl, 0)} · ${opp.classes.blunder} blunders, ${opp.classes.mistake} mistakes`)}</div>` : `<p class="muted">This game was not engine-analysed.</p>`}
          <div class="movelist" id="movelist">${movesHtml.join("")}</div>
          ${crit ? `<h3 style="margin-top:.8rem">Critical moments</h3><ul class="critical" id="critical">${crit}</ul>` : ""}
        </div>
      </div>`;
    viewer.board = new ChessBoard.Board($("#board"), { flipped: g.player_color === "black" });
    $("#b-start").onclick = () => gotoPly(0); $("#b-prev").onclick = () => gotoPly(viewer.ply - 1);
    $("#b-next").onclick = () => gotoPly(viewer.ply + 1); $("#b-end").onclick = () => gotoPly(an.moves.length);
    $("#b-flip").onclick = () => { viewer.board.flip(); };
    document.querySelectorAll("#movelist .mv").forEach(el => el.addEventListener("click", () => gotoPly(parseInt(el.dataset.ply, 10))));
    document.querySelectorAll("#critical li").forEach(el => el.addEventListener("click", () => gotoPly(parseInt(el.dataset.ply, 10))));
    if (an.eval_curve) {
      const marks = an.moves.filter(m => m.class === "blunder" || m.class === "mistake").map(m => ({ index: m.ply, value: an.eval_curve[m.ply], color: `var(--c-${m.class})` }));
      viewer.evalSvg = Charts.evalChart(an.eval_curve, { height: 110, marks, onSelect: gotoPly, tipFor: i => i === 0 ? "Start" : `${Math.ceil(i / 2)}${i % 2 ? "." : "…"} ${an.moves[i - 1].san} · ${an.moves[i - 1].eval_text}` });
      attachChart("eval-chart", viewer.evalSvg);
    }
    $("#viewer").hidden = false;
    gotoPly(ply != null ? ply : 0);
  }
  function gotoPly(p) {
    const an = viewer.data.analysis;
    p = Math.max(0, Math.min(an.moves.length, p));
    viewer.ply = p;
    const m = p > 0 ? an.moves[p - 1] : null;
    const fen = m ? m.fen : (an.start_fen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    const marks = {};
    const arrows = [];
    if (m) { marks[m.uci.slice(0, 2)] = "last"; marks[m.uci.slice(2, 4)] = "last"; }
    // the board shows the position the *next* move was played from: if that move was an error, show what was better
    const next = p < an.moves.length ? an.moves[p] : null;
    if (next && next.best && next.best !== next.uci && ["inaccuracy", "mistake", "blunder"].includes(next.class)) {
      arrows.push({ from: next.best.slice(0, 2), to: next.best.slice(2, 4), color: "var(--good)" });
      arrows.push({ from: next.uci.slice(0, 2), to: next.uci.slice(2, 4), color: `var(--c-${next.class})` });
    }
    viewer.board.setPosition(fen, marks, arrows);
    // eval bar
    let cp = m ? m.eval : (an.eval_curve ? an.eval_curve[0] : 0);
    if (cp != null) {
      const w = 50 + 50 * (2 / (1 + Math.exp(-0.004 * Math.max(-1000, Math.min(1000, cp)))) - 1);
      $("#evalbar .white").style.height = `${w}%`;
      $("#evalbar .txt").textContent = m ? m.eval_text : "";
      $("#evalbar .txt").style.top = w > 50 ? "auto" : "2px"; $("#evalbar .txt").style.bottom = w > 50 ? "2px" : "auto";
    }
    document.querySelectorAll("#movelist .mv").forEach(el => el.classList.toggle("cur", parseInt(el.dataset.ply, 10) === p));
    const cur = document.querySelector("#movelist .mv.cur"); if (cur) cur.scrollIntoView({ block: "nearest" });
    if (viewer.evalSvg) viewer.evalSvg.setMarker(p);
    const info = $("#move-info");
    const prevFen = p > 1 ? an.moves[p - 2].fen : an.start_fen;
    const mine = c => c === viewer.data.game.player_color;
    const nextNote = arrows.length ? `<div class="small" style="margin-top:.35rem"><span style="color:var(--good)">▶</span> The arrows show the next move (move ${Math.ceil(next.ply / 2)}, ${mine(next.color) ? "you" : "opponent"}): <span style="color:var(--bad);font-weight:600">${esc(describe(fen, next.uci))}</span> was played (${next.class}); <span style="color:var(--good);font-weight:600">${esc(describe(fen, next.best))}</span> was better.</div>` : "";
    if (!m) { info.innerHTML = `<div class="headline">Starting position</div><div class="muted small">Step through the game with the arrow keys or click a move.</div>${nextNote}`; return; }
    const said = `<div>${mine(m.color) ? "You" : "Opponent"}: ${esc(describe(prevFen, m.uci))}.</div>`;
    const tags = (m.tags || []).filter(t => !["opening", "middlegame", "endgame"].includes(t)).map(t => `<span class="tag">${esc(TAGS[t] || t)}</span>`).join("");
    const clock = m.clock != null ? ` · clock ${secs(m.clock)}${m.time_spent != null ? ` (spent ${secs(m.time_spent)})` : ""}` : "";
    if (!m.class) { info.innerHTML = `<div class="headline">${moveNo(m.ply)} ${esc(m.san)}</div>${said}<div class="muted small">${cap(m.phase)}${clock}</div>${nextNote}`; return; }
    const better = m.best && m.best !== m.uci && m.class !== "best" ? `<div><span style="color:var(--good);font-weight:600">Better</span>: ${esc(describe(prevFen, m.best))}${m.pv && m.pv.length ? ` <span class="muted small">(${esc(m.pv.join(" "))})</span>` : ""}</div>` : (m.class === "best" ? `<div class="muted small">Engine's first choice.</div>` : "");
    info.innerHTML = `<div class="headline"><span>${moveNo(m.ply)} ${esc(m.san)}</span><span class="cls ${m.class}">${m.class}</span><span class="muted small">${m.eval_text} · win chance ${fmt(m.win_before, 0)}% → ${fmt(m.win_after, 0)}%${m.cp_loss ? ` · lost ${m.cp_loss} cp` : ""}</span></div>
      ${said}${better}<div class="muted small">${cap(m.phase)}${clock}</div><div style="margin-top:.3rem">${tags}</div>${nextNote}`;
  }
  const TAGS = { missed_mate: "Missed a forced mate", allowed_mate: "Allowed a forced mate", hung_piece: "Hung a piece", lost_material: "Lost material", missed_material: "Missed a material win", bad_trade: "Bad trade", walked_into_fork: "Walked into a fork", threw_away_win: "Threw away a winning position", collapsed: "Went from equal to lost", time_trouble: "Time trouble", rushed: "Rushed (under 2s)", missed_opponent_blunder: "Missed the opponent's blunder" };

  // ---------- training (puzzles) --------------------------------------------------------------
  const SRS = {
    key: () => `puzzles:${state.username}`,
    server: null,           // progress loaded from the account, when signed in
    _timer: null,
    load() { if (this.server) return this.server; try { return JSON.parse(localStorage.getItem(this.key()) || "{}"); } catch (e) { return {}; } },
    save(d) {
      try { localStorage.setItem(this.key(), JSON.stringify(d)); } catch (e) { /* ignore */ }
      if (state.account) {
        this.server = d;
        clearTimeout(this._timer);
        this._timer = setTimeout(() => api(`/api/me/puzzles/${encodeURIComponent(state.username)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ progress: d }) }).catch(() => {}), 400);
      }
    },
    async sync() {
      // Signed in: the account's progress wins; a device's local progress is merged in the first time.
      this.server = null;
      if (!state.account) return;
      try {
        const r = await api(`/api/me/puzzles/${encodeURIComponent(state.username)}`);
        let local = {}; try { local = JSON.parse(localStorage.getItem(this.key()) || "{}"); } catch (e) { /* ignore */ }
        const merged = { ...local, ...(r.progress || {}) };
        this.server = merged;
        if (Object.keys(local).length && Object.keys(r.progress || {}).length !== Object.keys(merged).length) this.save(merged);
      } catch (e) { this.server = null; }
    },
    id: p => `${p.game_id}:${p.ply}`,
    record(p, ok) {
      const d = this.load(); const e = d[this.id(p)] || { box: 0, seen: 0, fails: 0 };
      e.seen++; if (ok) e.box = Math.min(5, e.box + 1); else { e.box = 0; e.fails++; }
      e.next = Date.now() + [0, 1, 3, 7, 14, 30][e.box] * 86400000; e.last = Date.now();
      d[this.id(p)] = e; this.save(d); return e;
    },
    order(list) {
      const d = this.load(), now = Date.now();
      // failed and due first, then unseen, then not-yet-due; keep the report's own priority inside each group
      const rank = p => { const e = d[this.id(p)]; if (!e) return 1; if (e.next <= now) return e.fails ? -1 : 0; return 2; };
      return list.map((p, i) => ({ p, i })).sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i).map(x => x.p);
    },
  };
  function renderTraining() {
    if (state.account && SRS.server === null && !renderTraining.syncing) {
      renderTraining.syncing = true;
      SRS.sync().then(() => { renderTraining.syncing = false; if (state.tab === "training") renderTab(); });
    }
    const r = state.report, ps = r.puzzles || [];
    if (!ps.length) { mount(head("Training") + `<section class="panel"><p class="muted">No puzzles yet: puzzles are built from engine-analysed games where you missed a clearly better move.</p></section>`); return; }
    const themes = ["all", ...new Set(ps.map(p => p.theme))];
    const T = state.puzzles;
    const list = SRS.order(ps.filter(p => T.theme === "all" || p.theme === T.theme));
    if (T.index >= list.length) T.index = 0;
    const srs = SRS.load();
    const due = list.filter(p => srs[SRS.id(p)] && srs[SRS.id(p)].next <= Date.now()).length;
    const unseen = list.filter(p => !srs[SRS.id(p)]).length;
    mount(`${head("Training", "Positions from your own games where you went wrong. Find the move the engine preferred: click the piece, then the destination square. Positions you fail come back sooner; positions you solve return after 1, 3, 7, 14 and 30 days.")}
      <div class="tiles" style="margin-bottom:.8rem">${tile("Due for review", due, "solved before, time to repeat")}${tile("New", unseen, "never attempted")}${tile("Verified", ps.filter(p => p.verified).length, "checked with three engine lines")}</div>
      ${legend()}<div style="height:.8rem"></div>
      <div class="theme-chips">${themes.map(t => `<button class="btn small ${T.theme === t ? "on" : ""}" data-theme="${esc(t)}">${esc(t === "all" ? `All (${ps.length})` : `${TAGS[t] || t} (${ps.filter(p => p.theme === t).length})`)}</button>`).join("")}</div>
      <div class="puzzle-layout">
        <div><div id="pboard"></div>
          <div class="puzzle-nav"><button class="btn small" id="p-prev">‹ Prev</button><span class="muted small" id="p-count"></span><button class="btn small" id="p-next">Next ›</button><span class="spacer" style="flex:1"></span><button class="btn small" id="p-solution">Show solution</button><button class="btn small" id="p-game">Open game</button></div>
        </div>
        <div class="panel"><div id="p-prompt"></div><div class="puzzle-status" id="p-status"></div><div id="p-detail" class="muted small"></div></div>
      </div>`);
    document.querySelectorAll("[data-theme]").forEach(b => b.addEventListener("click", () => { T.theme = b.dataset.theme; T.index = 0; renderTraining(); }));
    if (!list.length) return;
    const board = new ChessBoard.Board($("#pboard"), { interactive: true });
    let sel = null, done = false;
    const show = () => {
      const p = list[T.index];
      sel = null; done = false;
      const hist = SRS.load()[SRS.id(p)];
      board.flipped = p.side === "black";
      board.setPosition(p.fen, {}, []);
      $("#p-count").textContent = `${T.index + 1} / ${list.length}`;
      const alts = (p.accepted_san || []).length > 1 ? ` <span class="pill">${p.accepted_san.length} answers accepted</span>` : (p.only_move ? ' <span class="pill">only move</span>' : "");
      const playedUci = (p.played_uci || "");
      $("#p-prompt").innerHTML = `<div class="eyebrow">${esc(p.theme_label)}</div><h3>${cap(p.side)} to move${alts}</h3><p class="muted small">vs ${esc(p.opponent)} · ${dateOf(p.date)} · move ${Math.ceil(p.ply / 2)}. In the game you played ${playedUci ? esc(describe(p.fen, playedUci)) : `<strong class="mono">${esc(p.played)}</strong>`} and lost ${fmt(p.win_loss, 0)}% win chance. Find the better move: click the piece, then the square.${hist ? ` Attempted ${hist.seen}×, ${hist.fails} fail${hist.fails === 1 ? "" : "s"}.` : ""}</p>`;
      $("#p-status").textContent = "";
      $("#p-detail").textContent = "";
    };
    const reveal = (p) => {
      done = true;
      const marks = {}, arrows = [];
      (p.accepted || [p.best]).forEach((u, i) => { marks[u.slice(0, 2)] = "ok"; marks[u.slice(2, 4)] = "ok"; arrows.push({ from: u.slice(0, 2), to: u.slice(2, 4), color: i ? "var(--info)" : "var(--good)" }); });
      board.setPosition(p.fen, marks, arrows);
      const others = (p.accepted || []).slice(1).map(u => describe(p.fen, u));
      $("#p-detail").innerHTML = `<span style="color:var(--good);font-weight:600">Best</span>: ${esc(describe(p.fen, p.best))} (${esc(p.best_san)}).${others.length ? ` <span style="color:var(--info);font-weight:600">Also fine</span>: ${esc(others.join("; "))}.` : ""} <span class="muted">Engine line: ${esc(p.pv.join(" "))}.</span>`;
    };
    let wrongTries = 0;
    board.onSquare = sq => {
      const p = list[T.index];
      if (done) return;
      if (!sel) { sel = sq; board.setPosition(p.fen, { [sq]: "sel" }, []); return; }
      const guess = sel + sq;
      const ok = (p.accepted || [p.best]).some(u => u.slice(0, 4) === guess);
      if (ok) {
        SRS.record(p, wrongTries === 0); wrongTries = 0;
        $("#p-status").textContent = guess === p.best.slice(0, 4) ? "Correct!" : "Correct (an alternative the engine rates just as well).";
        reveal(p);
      } else if (sq === sel) { sel = null; board.setPosition(p.fen, {}, []); }
      else {
        wrongTries++;
        $("#p-status").textContent = `${sel}${sq} is not it. Try again.`;
        board.setPosition(p.fen, { [sel]: "wrong", [sq]: "wrong" }, []);
        sel = null;
      }
    };
    $("#p-prev").onclick = () => { T.index = (T.index - 1 + list.length) % list.length; show(); };
    $("#p-next").onclick = () => { T.index = (T.index + 1) % list.length; show(); };
    $("#p-solution").onclick = () => { if (!done) SRS.record(list[T.index], false); $("#p-status").textContent = "Solution"; reveal(list[T.index]); };
    $("#p-game").onclick = () => openGame(list[T.index].game_id, list[T.index].ply);
    show();
  }

  // ---------- boot ----------------------------------------------------------------------------
  loadStatus().then(route);
})();
