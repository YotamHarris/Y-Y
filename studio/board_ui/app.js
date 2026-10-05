// The board's browser UI (D116). Plain JS, no build step, no network beyond
// the local server. Every write is a POST acting as "owner".
"use strict";

const S = {
  state: null, last: 0, view: "inbox", drawer: null, drafts: {}, docs: {}, reading: null,
  tag: null, noteFilter: "open", threadWho: "all", showDropped: false, editing: {},
  boardScroll: { left: 0, top: 0 }, boardPanning: false, renderPending: false,
};
const COLS = ["idea", "ready", "claimed", "in_progress", "review", "blocked", "done"];
const LABEL = { in_progress: "in progress" };
const $ = (sel, root = document) => root.querySelector(sel);

// ------------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function clock(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? hm : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`;
}

const IMG_RE = /!\[([^\]]*)\]\((\/files\/[0-9a-f]{20}\.(?:png|jpg|gif|webp))\)/g;

function imgTag(alt, src) {
  return `<img class="shot" src="${src}" alt="${esc(alt)}" title="${esc(alt)} (click to enlarge)" loading="lazy">`;
}

function inline(text) {
  // code spans first so nothing inside them is formatted
  return String(text).split(/(`[^`]+`)/).map(part => {
    if (/^`[^`]+`$/.test(part)) return `<code>${esc(part.slice(1, -1))}</code>`;
    const imgs = [];
    let t = esc(part.replace(IMG_RE, (_, alt, src) => { imgs.push(imgTag(alt, src)); return `\u0000${imgs.length - 1}\u0000`; }));
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    t = t.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    t = t.replace(/\b([TMN])(\d+)\b/g, '<a class="ref" href="#" data-ref="$1$2">$1$2</a>');
    t = t.replace(/\b([DV]\d+)\b/g, '<span class="chip dv">$1</span>');
    return t.replace(/\u0000(\d+)\u0000/g, (_, i) => imgs[+i]);
  }).join("");
}

function md(src) {
  const lines = String(src ?? "").replace(/\r/g, "").split("\n");
  const out = [];
  let para = [], list = null, quote = [];
  const flushPara = () => { if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; };
  const flushList = () => { if (list) out.push(`<${list.tag}>${list.items.map(i => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; };
  const flushQuote = () => { if (quote.length) out.push(`<blockquote>${quote.map(inline).join("<br>")}</blockquote>`); quote = []; };
  const flush = () => { flushPara(); flushList(); flushQuote(); };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      const code = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flush(); out.push(`<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`); continue; }
    if ((m = line.match(/^\s*([-*]|\d+[.)])\s+(.*)/))) {
      flushPara(); flushQuote();
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[2]);
      continue;
    }
    if ((m = line.match(/^>\s?(.*)/))) { flushPara(); flushList(); quote.push(m[1]); continue; }
    if (!line.trim()) { flush(); continue; }
    if (list && /^\s{2,}/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    flushList(); flushQuote();
    para.push(line);
  }
  flush();
  return `<div class="md">${out.join("")}</div>`;
}

function toast(text, err = false) {
  const t = $("#toast");
  t.textContent = text;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), err ? 5000 : 1800);
}

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Board": "1" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j.error) { toast(j.error || `HTTP ${r.status}`, true); throw new Error(j.error); }
  await load();
  return j;
}

// ------------------------------------------------------------------ data

const byId = (list, id) => list.find(x => x.id === id);
const task = id => byId(S.state.tasks, id);
const note = id => byId(S.state.notes, id);
const msg = id => byId(S.state.messages, id);
const agents = () => S.state.people.filter(p => p !== "owner");

function threads() {
  const map = new Map();
  for (const m of S.state.messages) {
    if (!map.has(m.thread)) map.set(m.thread, []);
    map.get(m.thread).push(m);
  }
  return [...map.values()].sort((a, b) => b[b.length - 1].id - a[a.length - 1].id);
}

function taskUnread(t) {
  return S.state.messages.some(m => m.topic === t.ref && m.unread);
}

function presenceState(name) {
  const p = S.state.presence[name];
  if (!p || !p.heartbeat) return "unseen";
  if (p.state === "idle") return "idle";
  if (Date.now() / 1000 - p.heartbeat > 600) return "stale";
  return p.state || "working";
}

// D173: an agent's state is its sessions' (Claude's from its hooks, Codex's
// from Codex's own records, newest first); presence is the fallback.
function sessionsOf(name) {
  return (S.state.sessions || []).filter(s => s.agent === name);
}

function sessionState(s) {
  if (s.state !== "working") return "idle";
  return Date.now() / 1000 - s.heartbeat < s.limit ? "working" : "stale";
}

function agentState(name) {
  const states = sessionsOf(name).map(sessionState);
  if (states.includes("working")) return "working";
  return states[0] || presenceState(name);
}

// D210: the manager's runs on an agent's checkout, and the one a session is
// D218: the model a managed task runs on (or will next), as a chip
// D238: a tier is a model and the CLI that runs it; the labels come from the manager (models.labels).
function tierOf(label) { return /sonnet/i.test(label || "") ? "sonnet" : /opus/i.test(label || "") ? "opus" : /\bsol\b/i.test(label || "") ? "sol" : ""; }
function tierLabels() { return (((S.state.outlook || {}).models || {}).labels) || { sonnet: "Sonnet 5.5", opus: "Opus", sol: "GPT-6.1 Sol" }; }
function tierLabel(t) { return tierLabels()[t] || t; }
function modelChip(label, why = "", escalated = false) {
  if (!label) return "";
  return `<span class="chip model ${tierOf(label)}${escalated ? " escalated" : ""}" title="${esc(why)}">${esc(label)}</span>`;
}
function taskModel(id) { return (S.state.task_models || {})[id]; }
function mins(s) { s = Math.round(s || 0); return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m` : s >= 60 ? `${Math.floor(s / 60)}m` : `${s}s`; }
function tokens(n) { n = n || 0; return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n); }
function spendLine(m) {
  if (!m || !m.runs) return "";
  const fed = m.tokens - m.output_tokens;
  const waits = [["GPU", m.gpu_wait_s], ["frozen game", m.frozen_s]].filter(w => w[1] >= 1).map(w => `${w[0]} ${mins(w[1])}`).join(", ");
  return `<div class="spend"><b title="${m.running ? "estimated until the running turn ends" : ""}">${m.running ? "~" : ""}${tokens(m.output_tokens)}</b> tokens out, ${tokens(fed)} in ·
    working <b>${mins(m.working_s)}</b> (model ${mins(m.model_s)}, tools ${mins(m.tool_s)}${m.gpu_s >= 1 ? `, GPU ${mins(m.gpu_s)}` : ""})
    ${m.waiting_s >= 1 ? `· waiting <b>${mins(m.waiting_s)}</b> (${waits})` : ""}${m.queue_s >= 60 ? ` · queued ${mins(m.queue_s)}` : ""}
    ${m.runs > 1 ? ` · ${m.runs} runs` : ""}</div>`;
}

function runsOn(name) {
  return ((S.state.outlook && S.state.outlook.running) || []).filter(r => r.agent === name);
}

function runOf(s, runs) {
  return runs.find(r => r.session && (s.id === r.session || s.id === "codex:" + r.session));
}

function sessionLine(s, run) {
  const st = sessionState(s);
  let what;
  if (s.kind === "codex") {
    const title = String(s.title || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    what = `Codex${s.model ? ` <span class="chip">${esc(s.model)}</span>` : ""}${title ? ` ${esc(title.length > 70 ? title.slice(0, 69) + "…" : title)}` : ""}`;
    if (s.turn === "working" && s.turn_at && st === "working") what += ` <span class="muted small">turn running ${ago(s.turn_at).replace(" ago", "")}</span>`;
  } else {
    what = `Claude Code <span class="files">${esc(s.id.slice(0, 8))}</span>`;
  }
  if (run) what += ` <span class="chip">manager: ${esc(run.name)}</span>`;
  return `<div><span class="state ${st}">${st}</span> ${what} <span class="muted small">· last ${ago(s.heartbeat)}</span></div>`;
}

function counterpart(list) {
  // who a reply from the owner goes to: the last non-owner voice in the thread
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    if (m.sender !== "owner") return m.sender;
    if (m.recipient !== "owner") return m.recipient;
  }
  return "all";
}

// ------------------------------------------------------------------ loading

// The project the board serves (studio.toml, /api/state's project): its name and what a game is called.
function project() {
  return (S.state && S.state.project) || {name: document.querySelector(".brand").firstChild.textContent.trim(), process_label: "games"};
}

async function load() {
  const r = await fetch("/api/state");
  if (!r.ok) { toast(`board: ${(await r.json().catch(() => ({}))).error || r.status}`, true); return; }
  S.state = await r.json();
  S.last = S.state.last_event;
  render();
}

async function poll() {
  try {
    const r = await fetch(`/api/events?since=${S.last}`);
    const j = await r.json();
    if (j.last !== S.last || S.view === "manager") await load();
    if (S.view === "nightly" && Date.now() - (S.nightlyAt || 0) > 5000) loadNightly();
  } catch (e) { /* server restarting */ }
}

// ------------------------------------------------------------------ render

function render() {
  if (!S.state) return;
  if (S.boardPanning) { S.renderPending = true; return; }
  S.renderPending = false;
  const oldBoard = $(".board");
  if (oldBoard) S.boardScroll = { left: oldBoard.scrollLeft, top: oldBoard.scrollTop };
  const active = document.activeElement;
  const focusKey = active && active.dataset && active.dataset.draft;
  const range = focusKey && "selectionStart" in active ? [active.selectionStart, active.selectionEnd] : null;

  document.querySelectorAll("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.view === S.view));
  document.body.classList.toggle("board-view", S.view === "board");
  badges();
  if (S.view === "log") { if (!$("#log")) openLog(LOG.ref); renderDrawer(); return; }
  const views = { inbox: viewInbox, board: viewBoard, thoughts: viewThoughts, threads: viewThreads, agents: viewAgents, manager: viewManager, nightly: viewNightly, doc: viewDoc };
  $("#view").innerHTML = (views[S.view] || viewInbox)();
  renderDrawer();

  document.querySelectorAll("[data-draft]").forEach(el => {
    const k = el.dataset.draft;
    if (k in S.drafts && el.value !== S.drafts[k]) el.value = S.drafts[k];
  });
  if (focusKey) {
    const el = document.querySelector(`[data-draft="${CSS.escape(focusKey)}"]`);
    if (el) { el.focus(); if (range) try { el.setSelectionRange(range[0], range[1]); } catch (e) { } }
  }
  if (S.view === "board") {
    const board = $(".board");
    board.scrollLeft = S.boardScroll.left;
    board.scrollTop = S.boardScroll.top;
    wirePan(board);
    wireDrag();
  }
  updatePreviews();
}

function badges() {
  const st = S.state;
  const needs = st.open_questions.length + st.tasks.filter(t => t.status === "review").length;
  const unread = st.messages.filter(m => m.unread && !st.open_questions.includes(m.id)).length;
  $("#badge-inbox").textContent = needs + unread || "";
  $("#badge-board").textContent = st.tasks.filter(t => t.status === "review").length || "";
  $("#badge-threads").textContent = st.messages.filter(m => m.unread).length || "";
  $("#badge-agents").innerHTML = agents().filter(a => a !== "manager").map(a => `<span class="state ${agentState(a)}" title="${esc(a)}: ${agentState(a)}"></span>`).join("");
  const o = st.outlook;
  $("#badge-manager").innerHTML = !o ? "" : !o.online ? `<span class="state stale" title="the manager is not stepping"></span>`
    : o.running.map(r => `<span class="state working" title="${esc(r.name)}: ${esc(r.title || "")}"></span>`).join("");
  document.title = (needs + unread ? `(${needs + unread}) ` : "") + `${project().name} Board`;
}

function msgCard(m, opts = {}) {
  const topic = m.topic ? `<a class="ref" href="#" data-ref="${esc(m.topic)}">${esc(m.topic)}</a>` : "";
  const cls = ["card", opts.pinned ? "pinned" : m.unread ? "unread" : ""].join(" ");
  const reply = opts.reply ? `
    <div class="composer">
      <textarea data-draft="reply-${m.id}" placeholder="Answer ${esc(m.sender)}…  (Ctrl+Enter sends)"></textarea>
      <div class="row"><button class="primary" data-act="reply" data-id="${m.id}">Reply to ${esc(m.sender)}</button>${attachBtn(`reply-${m.id}`)}
      <span class="spacer"></span>${m.unread ? `<button data-act="read" data-id="${m.id}">Mark read</button>` : ""}
      <button data-act="open-thread" data-id="${m.thread}">Open thread</button></div>
    </div>` : "";
  return `<div class="${cls}">
    <div class="meta"><span class="kind ${esc(m.kind)}">${esc(m.kind)}</span>
      <b>${esc(m.sender)}</b> → ${esc(m.recipient)} ${topic}<span class="spacer"></span>
      <span title="${esc(new Date(m.created * 1000).toLocaleString())}">${ago(m.created)}</span>
      <a class="ref" href="#" data-ref="M${m.id}">M${m.id}</a></div>
    <div class="subject">${esc(m.subject)}</div>
    ${m.body ? md(m.body) : ""}
    ${opts.actions ? `<div class="actions">${m.unread ? `<button data-act="read" data-id="${m.id}">Mark read</button>` : ""}
      <button data-act="open-thread" data-id="${m.thread}">Reply…</button></div>` : ""}
    ${reply}
  </div>`;
}

function viewInbox() {
  const st = S.state;
  const open = st.open_questions.map(msg).filter(Boolean);
  const review = st.tasks.filter(t => t.status === "review");
  const unread = st.messages.filter(m => m.unread && !st.open_questions.includes(m.id)).reverse();
  const needs = open.map(m => msgCard(m, { pinned: true, reply: true })).join("") +
    review.map(t => `<div class="card pinned clickable" data-open-task="${t.id}">
      <div class="meta"><span class="kind review">review</span><b>${esc(t.claimed_by || t.assignee)}</b> finished
      <a class="ref" href="#" data-ref="${t.ref}">${t.ref}</a><span class="spacer"></span>${ago(t.updated)}</div>
      <div class="subject">${esc(t.title)}</div>
      <div class="meta">${t.links.map(linkChip).join(" ")}</div>
      <div class="actions"><button class="primary" data-act="accept" data-id="${t.id}">Accept → done</button>
      <button data-act="open-task" data-id="${t.id}">Look / send back…</button></div></div>`).join("");
  const events = st.events.map(e => `<li><span class="t">${clock(e.at)}</span><span class="a">${esc(e.actor)}</span>
    <span>${e.ref ? `<a class="ref" href="#" data-ref="${esc(e.ref)}">${esc(e.ref)}</a> ` : ""}${esc(e.summary || e.kind)}</span></li>`).join("");
  return `<div class="two"><div>
    <h2>Needs you</h2>${needs || `<div class="empty">Nothing is waiting on you.</div>`}
    <h2>Unread</h2>${unread.map(m => msgCard(m, { actions: true })).join("") || `<div class="empty">All read.</div>`}
  </div><div>
    <h2>Activity</h2><ul class="events">${events || `<li class="empty">Nothing yet.</li>`}</ul>
  </div></div>`;
}

function dependencyLink(id) {
  const dep = task(id);
  return `<a class="dependency" href="#T${id}" data-ref="T${id}" draggable="false">
    <span class="ref">T${id}</span><span class="dependency-title">${esc(dep ? dep.title : "Task unavailable")}</span>
    <span class="status">${esc(dep ? (LABEL[dep.status] || dep.status) : "unknown")}</span></a>`;
}

function blockingDependencies(t) {
  return t.depends.filter(id => !["done", "dropped"].includes(task(id)?.status));
}

function dependencyDetails(t) {
  const blocked = blockingDependencies(t);
  const resolved = t.depends.filter(id => !blocked.includes(id));
  const dependents = S.state.tasks.filter(other => other.depends.includes(t.id));
  return `${blocked.length ? `<section class="dependencies blocking"><h2>Blocked by ${blocked.length} ${blocked.length === 1 ? "task" : "tasks"}</h2>
    <p class="small muted">These tasks must be done or dropped before this task can be claimed.</p>
    ${blocked.map(dependencyLink).join("")}</section>` : ""}
    ${resolved.length ? `<section class="dependencies"><h2>Resolved dependencies</h2>${resolved.map(dependencyLink).join("")}</section>` : ""}
    ${dependents.length ? `<section class="dependencies"><h2>Required by</h2>${dependents.map(other => dependencyLink(other.id)).join("")}</section>` : ""}`;
}

// A task's link chip; "Goal G3" reads "G3 · V165 improvements" once the goal has a name (D193).
function linkChip(l) {
  const g = /^Goal G(\d+)$/.exec(l);
  const goal = g && ((S.state.manager || {}).goals || []).find(x => String(x.id) === g[1]);
  return `<span class="chip dv"${goal ? ` title="Goal G${goal.id}"` : ""}>${esc(goal && goal.name ? `G${goal.id} · ${goal.name}` : l)}</span>`;
}

function taskCard(t) {
  const who = t.claimed_by || (t.assignee !== "any" ? t.assignee : "");
  const blocked = blockingDependencies(t);
  return `<div class="tcard" draggable="true" data-task="${t.id}">
    <div class="meta"><span class="ref">${t.ref}</span><span class="prio p${t.priority}">P${t.priority}</span>
      ${who ? `<span class="who">${esc(who)}</span>` : ""}${(m => m ? modelChip(m.label || m.next, m.why, m.escalations > 0) : "")(taskModel(t.id))}${taskUnread(t) ? `<span class="dot" title="unread discussion"></span>` : ""}
      </div>
    <div class="title">${esc(t.title)}</div>
    ${blocked.length ? `<div class="dependencies blocking"><b class="small">Blocked by</b>${blocked.map(dependencyLink).join("")}</div>`
      : t.depends.length ? `<div class="small muted">Dependencies resolved</div>` : ""}
    ${cardThumb(t)}
    ${t.links.length ? `<div class="meta">${t.links.slice(0, 4).map(linkChip).join("")}</div>` : ""}
  </div>`;
}

function taskImages(t) {
  const text = [t.body, ...S.state.messages.filter(m => m.topic === t.ref).map(m => m.body)].join("\n");
  return [...text.matchAll(IMG_RE)].map(m => ({ alt: m[1], src: m[2] }));
}

function cardThumb(t) {
  const imgs = taskImages(t);
  if (!imgs.length) return "";
  return `<div class="thumb"><img src="${imgs[0].src}" alt="${esc(imgs[0].alt)}" loading="lazy" draggable="false">
    ${imgs.length > 1 ? `<span>+${imgs.length - 1}</span>` : ""}</div>`;
}

function viewBoard() {
  const cols = S.showDropped ? [...COLS, "dropped"] : COLS;
  const body = cols.map(c => {
    let ts = S.state.tasks.filter(t => t.status === c);
    if (c === "done") ts = ts.sort((a, b) => b.updated - a.updated).slice(0, 20);
    return `<section class="col" data-col="${c}"><h3><span>${LABEL[c] || c}</span><span>${ts.length}</span></h3>
      ${ts.map(taskCard).join("")}</section>`;
  }).join("");
  return `<div class="row" style="margin-bottom:12px"><button class="primary" data-act="new-task">+ New task</button>
    <span class="muted small">Left-drag empty space to pan. Drag cards between columns; drop on a card to put it before that one (it takes that card's priority).</span>
    <span class="spacer"></span><label class="small"><input type="checkbox" data-act="toggle-dropped" ${S.showDropped ? "checked" : ""}> show dropped</label></div>
    <div class="board">${body}</div>`;
}

function viewThoughts() {
  const tags = [...new Set(S.state.notes.flatMap(n => n.tags ? n.tags.split(",") : []))].sort();
  let notes = S.state.notes.filter(n => S.noteFilter === "all" || n.status === S.noteFilter);
  if (S.tag) notes = notes.filter(n => n.tags.split(",").includes(S.tag));
  return `<div class="card capture-big">
      <textarea data-draft="thought" placeholder="A thought, an idea, something that bothers you…  Markdown works; paste or drop a screenshot. Ctrl+Enter saves."></textarea>
      <div class="row" style="margin-top:6px"><input data-draft="thought-tags" placeholder="tags, comma separated" style="flex:1">
      ${attachBtn("thought")}
      <button class="primary" data-act="save-thought">Save thought</button></div></div>
    <div class="row"><div class="tags">
      ${["open", "converted", "archived", "all"].map(f => `<button data-act="note-filter" data-f="${f}" class="${S.noteFilter === f ? "on" : ""}">${f}</button>`).join("")}
      <span class="muted" style="margin:0 6px">|</span>
      ${tags.map(t => `<button data-act="tag" data-tag="${esc(t)}" class="${S.tag === t ? "on" : ""}">#${esc(t)}</button>`).join("")}
    </div></div>
    ${notes.map(noteCard).join("") || `<div class="empty">No thoughts here.</div>`}`;
}

function noteCard(n) {
  const editing = S.editing["N" + n.id];
  const body = editing
    ? `<textarea data-draft="edit-N${n.id}">${esc(n.body)}</textarea>
       <input data-draft="edit-N${n.id}-tags" value="${esc(n.tags)}" placeholder="tags" class="wide" style="width:100%;margin-top:4px">`
    : md(n.body);
  return `<div class="card" id="N${n.id}">
    <div class="meta"><a class="ref" href="#" data-ref="N${n.id}">N${n.id}</a><b>${esc(n.author)}</b>
      <span>${ago(n.created)}</span>${n.tags ? n.tags.split(",").map(t => `<span class="chip">#${esc(t)}</span>`).join("") : ""}
      <span class="spacer"></span><span class="status">${esc(n.status)}</span>
      ${n.task_id ? `→ <a class="ref" href="#" data-ref="T${n.task_id}">T${n.task_id}</a>` : ""}</div>
    ${body}
    <div class="actions">
      ${editing ? `<button class="primary" data-act="save-note" data-id="${n.id}">Save</button><button data-act="cancel-edit" data-key="N${n.id}">Cancel</button>`
      : `${n.status !== "converted" ? `<button class="primary" data-act="note-to-task" data-id="${n.id}">Make task</button>` : ""}
         <button data-act="edit" data-key="N${n.id}">Edit</button>
         <button data-act="ask-about" data-id="${n.id}">Ask an agent…</button>
         ${n.status === "archived" ? `<button data-act="note-status" data-id="${n.id}" data-s="open">Reopen</button>`
          : `<button data-act="note-status" data-id="${n.id}" data-s="archived">Archive</button>`}`}
    </div></div>`;
}

function viewThreads() {
  const who = S.threadWho;
  const list = threads().filter(ms => who === "all" || ms.some(m => m.sender === who || m.recipient === who));
  const rows = list.map(ms => {
    const root = ms[0], last = ms[ms.length - 1];
    const people = [...new Set(ms.flatMap(m => [m.sender, m.recipient]))].join(", ");
    const unread = ms.filter(m => m.unread).length;
    return `<div class="card clickable ${unread ? "unread" : ""}" data-open-thread="${root.thread}">
      <div class="thread-row"><div><span class="kind ${esc(root.kind)}">${esc(root.kind)}</span>
        <b>${esc(root.subject)}</b> ${root.topic ? `<span class="chip">${esc(root.topic)}</span>` : ""}</div>
        <div class="muted small">${ago(last.created)}</div>
        <div class="muted small">${esc(people)} · ${ms.length} message${ms.length > 1 ? "s" : ""}${unread ? ` · <b style="color:var(--accent)">${unread} unread</b>` : ""}</div>
        <div class="muted small">last: ${esc(last.sender)}</div></div></div>`;
  }).join("");
  return `<div class="row" style="margin-bottom:12px"><button class="primary" data-act="new-msg">+ New message</button>
    <span class="spacer"></span><span class="muted small">involving</span>
    <select data-act="thread-who">${["all", ...S.state.people].map(p => `<option ${p === who ? "selected" : ""}>${p}</option>`).join("")}</select></div>
    ${rows || `<div class="empty">No messages yet.</div>`}`;
}

// The deadline a blocked provider waits for: the clock alone while it is within the
// day (every allowance block is), the date too for one that is not (authentication).
function retryAt(at) {
  if (!at) return "?";
  const d = new Date(at * 1000);
  const clock = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return at * 1000 - Date.now() < 86400e3 ? clock : `${d.toLocaleDateString()} ${clock}`;
}

// D210: a ref the outlook names, as a link when the board has it (goals have no drawer)
function outRef(r) {
  return /^T\d+$/.test(r) ? `<a class="ref" href="#" data-ref="${esc(r)}">${esc(r)}</a>` : `<b>${esc(r)}</b>`;
}

// how long since ts, without " ago": "25m"
function since(ts) { return ago(ts).replace(" ago", ""); }

function runSteps(r, n = 4) {
  return (r.steps || []).length ? `<ul class="steps">${r.steps.slice(-n).map(s => {
    const said = s.startsWith("said: ");
    return `<li class="${said ? "said" : "ran"}">${esc(s.replace(/^(said|ran): /, ""))}</li>`;
  }).join("")}</ul>` : `<p class="muted small">no steps logged yet</p>`;
}

function runCard(r) {
  return `<div class="card run">
    <div class="row"><b>${esc(r.name)}</b>${modelChip(r.model_label || r.provider, r.why)}${r.agent ? `<span class="chip">${esc(r.agent)}</span>` : ""}
      <span class="state ${r.state === "running" ? "working" : "idle"}">${esc(r.state)}</span>
      <span class="spacer"></span><span class="muted small">for ${since(r.started)} · last step ${ago(r.beat)}</span> ${logLink(logRef(r))}</div>
    <p>${r.task ? `${outRef("T" + r.task)} ` : r.goal ? `<b>G${r.goal}</b> ` : ""}${esc(r.title || "")}</p>
    ${r.task ? spendLine(taskModel(r.task)) : ""}
    ${runSteps(r)}</div>`;
}

function waitRow(w) {
  const docs = (w.docs || []).length ? w.why.replace(/ \(read [^)]*\)$/, "") : w.why;
  return `<div class="wait">${outRef(w.ref)} <span>${esc(w.title || "")}</span><div class="muted small">${esc(docs)}</div>
    ${(w.docs || []).map(p => `<div class="small">read <a href="${docHref(w.ref.slice(1), p)}">${esc(p)}</a></div>`).join("")}</div>`;
}

function inboxRow(e) {
  const became = esc(e.became).replace(/\b(T\d+)\b/g, '<a class="ref" href="#" data-ref="$1">$1</a>');
  return `<div class="wait"><span class="muted small">${ago(e.at)}${e.task ? ` · on ${outRef("T" + e.task)}` : ""}</span>
    <div>${e.body ? `“${esc(e.body)}”` : `<i>${esc(e.kind)}</i>`}</div><div class="small">→ ${became}</div></div>`;
}

// D218: what each model has delivered, the routing it leads to, and what would go faster
function modelsSection(s) {
  if (!s) return "";
  const tiers = Object.entries(s.tiers || {});
  const rows = Object.entries(s.policy || {}).map(([cat, tier]) => {
    const rec = (s.record || {})[cat] || {};
    const cells = Object.keys(s.labels || {}).filter(t => rec[t]).map(t => `${esc(tierLabel(t))} ${rec[t].landed}/${rec[t].tried}${rec[t].escalated ? ` (${rec[t].escalated} escalated)` : ""}`).join(", ");
    const over = ((s.handover || {})[cat]) || "";
    return `<tr><td>${esc(cat)}</td><td>${modelChip(tierLabel(tier))}</td><td>${cells || `<span class="muted">nothing finished yet</span>`}</td><td>${over ? modelChip(tierLabel(over)) : ""}</td></tr>`;
  }).join("");
  return `<h2>Models <span class="muted small">which model runs each task (D218)</span></h2><div class="card">
    ${s.planner ? `<p>Planner ${modelChip(s.planner, "plans every goal and picks each task's model")} <span class="muted small">each goal's planning transcript is under Goals; <code>fe_manager.py model planner sonnet|opus</code> sets it</span></p>` : ""}
    ${tiers.length ? `<table class="models"><tr><th>model</th><th>tasks</th><th>tokens out</th><th>median working</th><th>working</th><th>waiting</th></tr>
      ${tiers.map(([t, x]) => `<tr><td>${modelChip((s.labels || {})[t] || t)}</td><td>${x.tasks}</td><td>${tokens(x.output_tokens)}</td>
        <td>${mins(x.median_working_s)}</td><td>${mins(x.working_s)}</td><td>${mins(x.waiting_s)}</td></tr>`).join("")}</table>` : `<p class="muted">No metered runs yet.</p>`}
    <h3 class="small">Routing by kind of work <span class="muted">(landed/tried on its first model)</span></h3>
    <table class="models"><tr><th>work</th><th>goes to</th><th>record</th><th title="who takes a Sonnet run that fails or stalls">hand-over</th></tr>${rows}</table>
    ${(s.advice || []).length ? `<h3 class="small">To go faster</h3><ul class="small">${s.advice.map(a => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
    <p class="muted small">Set one task's model in its drawer, or <code>fe_manager.py model T12 sonnet|opus|sol|auto</code>; <code>fe_manager.py models</code> prints all of this.</p></div>`;
}

// D243: how much of each provider's allowance is spent, as manager_outlook words it
function usageSection(usage) {
  if (!usage || !usage.length) return "";
  const bar = w => `<div class="usage-win"><span>${esc(w.text)}</span><div class="usage-bar${w.stale ? " stale" : w.used >= 90 ? " hot" : w.used >= 70 ? " warm" : ""}"><i style="width:${Math.min(100, w.used)}%"></i></div></div>`;
  return `<h2>Allowance used <span class="muted small">the account's, so your own sessions count too</span></h2><div class="card">
    ${usage.map(u => `<div class="usage"><b>${esc(u.provider[0].toUpperCase() + u.provider.slice(1))}</b>
      ${u.state === "none" ? `<span class="muted">${esc(u.text)}</span>` : `${u.windows.map(bar).join("")}
        <div class="muted small">${esc(u.read)}${u.note ? ` · ${esc(u.note)}` : ""}${u.state === "stale" ? " · stale: the next run takes a fresh reading" : ""}</div>`}</div>`).join("")}</div>`;
}

function viewManager() {
  const m = S.state.manager || { mode: "not configured", runs: [], goals: [], providers: [] };
  const o = S.state.outlook;
  const live = o ? o.online : m.heartbeat && Date.now() / 1000 - m.heartbeat < 150;
  const out = o ? o.providers.filter(p => p.state !== "ready") : [];
  const yours = o ? o.waiting.filter(w => w.you) : [], queue = o ? o.waiting.filter(w => !w.you) : [];
  // the planner's model and its latest transcript, and each running worker's, first thing on the page
  const plan = m.runs.find(r => r.role === "planner"), goal = plan && m.goals.find(g => g.id === plan.goal_id);
  const planner = ((o || {}).models || {}).planner;
  const workers = m.runs.filter(r => r.role !== "planner" && ["queued", "running"].includes(r.state));
  const deliveries = S.state.mobile && S.state.mobile.deliveries || [];
  const deliveryLabels = { queued: "Queued", dispatching: "Waiting for GitHub", waiting_workflow: "Waiting for workflow", building: "Building and signing", processing: "Uploaded; Apple verification pending", ready: "Ready for internal testing", failed: "Delivery failed" };
  const mobileSection = !S.state.mobile ? "" : `<h2>TestFlight deliveries</h2>${deliveries.map(d => `<div class="card"><b>${esc(d.game)} · ${esc(deliveryLabels[d.state] || d.state)}</b> <code>${esc((d.sha || "").slice(0, 12))}</code>${d.task ? ` · <a href="#" data-ref="T${d.task}">T${d.task}</a>` : ""}${d.url && d.url.startsWith("https://github.com/") ? ` · <a href="${esc(d.url)}" target="_blank" rel="noopener">Workflow</a>` : ""}${d.error ? `<p>${esc(d.error)}</p>` : ""}</div>`).join("") || '<div class="card muted">No builds requested or published yet.</div>'}`;
  const whoRuns = `<div class="card manager-models">
    <p><b>Planner</b> ${planner ? modelChip(planner) : ""} ${plan ? `<code class="small">${esc(plan.model || plan.provider)}</code>
      · latest: ${plan.goal_id ? `G${plan.goal_id}${goal && goal.name ? ` ${esc(goal.name)}` : ""}` : "planning"} · ${ago(plan.heartbeat)}
      · ${logLink(plan.goal_id ? `G${plan.goal_id}` : plan.id, "<b>planner transcript</b>")}` : `<span class="muted">no planner run yet</span>`}</p>
    <p><b>Workers</b> ${workers.map(r => `<a class="ref" href="#" data-ref="T${r.task_id}">T${r.task_id}</a> <code class="small">${esc(r.model || r.provider)}</code> ${logLink(`T${r.task_id}`)}`).join(" · ") || `<span class="muted">none running</span>`}</p>
    <p class="muted small">Every goal's planning transcript is under Goals below; <code>fe_manager.py model planner sonnet|opus</code> sets the planner's model.</p></div>`;
  const now = !o ? "" : `<h2>Now <span class="muted small">${o.used} of ${o.slots} run slot${o.slots > 1 ? "s" : ""} in use${o.planners ? ` · ${o.planners} planning` : ""}</span></h2>
    ${o.running.map(runCard).join("") || `<div class="card muted">Nothing running${o.halt ? `: ${esc(o.halt)}` : ""}.</div>`}
    <div class="two"><div><h2>Waiting in the queue</h2><div class="card">${queue.map(waitRow).join("") || `<p class="muted">Nothing queued.</p>`}</div></div>
    <div><h2>Waiting on you</h2><div class="card">${yours.map(waitRow).join("") || `<p class="muted">Nothing.</p>`}</div></div></div>
    <h2>Your recent messages</h2><div class="card">${o.inbox.map(inboxRow).join("") || `<p class="muted">None yet.</p>`}</div>`;
  return `<h2>Models running <span class="muted small">the planner and the workers, with their transcripts</span></h2>${whoRuns}
    <h2>Background project manager</h2><div class="card">
    <p><b>${esc(m.mode)}</b> · ${live ? "connected" : "offline"} · last heartbeat ${ago(m.heartbeat)}</p>
    <div class="row">${["resume", "pause", "stop"].map(a => `<button data-act="manager-control" data-command="${a}">${a[0].toUpperCase() + a.slice(1)}</button>`).join("")}<button data-act="mobile-build">Build for TestFlight</button></div>
    <p class="muted">Pause finishes active runs. Stop interrupts managed workers and preserves their files. Resume enables dispatch when the service is online.</p>
    ${out.map(p => `<p style="color:var(--warn)">${esc(p.name)}: ${esc(p.why)}</p>`).join("")}
    ${o ? "" : m.providers.map(p => `<p>${esc(p.name)} waiting: ${esc(p.reason)}, retrying at ${esc(retryAt(p.retry_at))}</p>`).join("")}</div>
    ${o ? usageSection(o.usage) : ""}
    ${mobileSection}
    ${now}
    ${modelsSection(o && o.models)}
    <h2>Goals</h2>${m.goals.map(g => `<div class="card"><b>G${g.id}${g.name ? ` · ${esc(g.name)}` : ""}</b> <span class="muted small">${esc(g.status)} · ${ago(g.created)}</span>
      ${g.planner_runs ? `${g.planner_model ? `<code class="small">${esc(g.planner_model)}</code>` : ""} ${logLink(`G${g.id}`, `planner transcript (${g.planner_runs} run${g.planner_runs > 1 ? "s" : ""})`)}` : ""}${md(g.body)}</div>`).join("") || '<p>No goals yet. Send the manager a goal in Discord.</p>'}
    <h2>Runs</h2>${m.runs.map(r => `<div class="card"><b>${esc(r.model ? r.model : r.provider)} · ${esc(r.role)} · ${esc(r.state)}</b> <span class="muted small">last ${ago(r.heartbeat)}</span> ${logLink(r.task_id ? `T${r.task_id}` : r.id)}
      <p>${r.task_id ? `<a class="ref" href="#" data-ref="T${r.task_id}">T${r.task_id}</a>` : `Planning${r.goal_id ? ` G${r.goal_id}` : ""}`} ${esc(r.agent || "")}</p>
      ${r.error ? `<pre>${esc(r.error)}</pre>` : ""}</div>`).join("")}`;
}

// D191: the board is what holds a checkout. Edit the task to move the hold: drop it,
// close it or set it back to ready and the checkout is free; set it in progress to resume.
function holdLine(l) {
  if (l.route === undefined) return "…";
  const h = l.hold;
  if (!h) return `<span class="muted">no task</span>`;
  const why = h.run ? `its worker is ${esc(h.run)}` : h.status === "blocked" ? "blocked: waiting on you" : esc(h.status.replace("_", " "));
  return `<a class="ref" href="#" data-ref="T${h.task}">T${h.task}</a> ${esc(h.title || "")}
    <div class="muted small">${why} · manager phase ${esc(h.phase)} · drop it or set it to ready to free the checkout</div>`;
}

function routeLine(l) {
  if (l.route === undefined) return "…";
  const { busy, soft } = l.route;
  if (!busy.length) return `free${soft.length ? ` <span class="muted small">(${soft.map(esc).join("; ")})</span>` : ""}`;
  return `<b style="color:var(--warn)">sent elsewhere</b><div class="small">${busy.map(esc).join("<br>")}</div>`;
}

// D210: the manager is no checkout: its card is what it runs and what waits, in short
function managerCard() {
  const o = S.state.outlook;
  if (!o) return "";
  const yours = o.waiting.filter(w => w.you).length, queued = o.waiting.length - yours;
  const out = o.providers.filter(p => p.state !== "ready");
  return `<div class="card agent">
    <h3>manager <span class="state ${!o.online ? "stale" : o.running.length ? "working" : "idle"}">${o.online ? esc(o.mode) : "not stepping"}</span>
      <span class="spacer"></span><a href="#manager">Manager tab</a></h3>
    <div class="kv">
      <span>slots</span><span>${o.used} of ${o.slots} in use${out.map(p => `<div class="small" style="color:var(--warn)">${esc(p.name)}: ${esc(p.why)}</div>`).join("")}</span>
      <span>running</span><span>${o.running.map(r => `<div>${esc(r.name)}${r.agent ? ` in ${esc(r.agent)}` : ""} ${modelChip(r.model_label, r.why)} <span class="muted small">${since(r.started)}</span> ${logLink(logRef(r))}</div>`).join("") || `<span class="muted">nothing${o.halt ? `: ${esc(o.halt)}` : ""}</span>`}</span>
      <span>waiting</span><span>${queued} in the queue · ${yours} on you</span>
      <span>last tick</span><span>${ago(o.heartbeat)}</span>
    </div></div>`;
}

function viewAgents() {
  const live = (S.state.live && S.state.live.agents) || {};
  const cards = managerCard() + agents().filter(name => name !== "manager").map(name => {
    const p = S.state.presence[name] || {}, l = live[name] || {}, reg = S.state.registry[name] || {};
    const st = agentState(name), sess = sessionsOf(name);
    const files = [...(l.modified || []), ...(l.untracked || [])];
    // the last task it reported, while the board still has it in the work
    const reported = p.task_id ? task(p.task_id) : null;
    const task_ = reported && ["claimed", "in_progress", "blocked", "review"].includes(reported.status) ? reported : null;
    const held = S.state.tasks.filter(t => t.claimed_by === name && ["claimed", "in_progress", "blocked", "review"].includes(t.status));
    // `doing` said before the newest session began belongs to an earlier one: it is not
    // what the agent does now (a managed worker never says it), so it is not shown as such (D210)
    const earlier = p.activity && sess.length && (p.activity_at || 0) < (sess[0].started || sess[0].heartbeat);
    const runs = runsOn(name);
    const said = p.activity && !earlier
      ? `${inline(p.activity)}${p.activity_at ? ` <span class="muted small">said ${ago(p.activity_at)}</span>` : ""}` : "";
    const doing = runs.length ? runs.map(r => `<div><b>manager: ${esc(r.name)}</b>${r.task ? ` ${outRef("T" + r.task)}` : ""} ${esc(r.title || "")}
        <div class="muted small">${modelChip(r.model_label || r.provider, r.why)} ${esc(r.state)} for ${since(r.started)} · last step ${ago(r.beat)}</div>${r.task ? spendLine(taskModel(r.task)) : ""}${runSteps(r, 3)}</div>`).join("")
        + (said ? `<div class="small">${said}</div>` : "")
      : said || `<span class="muted" title="${earlier ? esc(`last said ${ago(p.activity_at)}, in an earlier session: ${p.activity}`) : ""}">—</span>`;
    const beat = Math.max(p.heartbeat || 0, ...sess.map(s => s.heartbeat)) || null;
    return `<div class="card agent">
      <h3>${esc(name)} <span class="state ${st}">${st}</span>${reg.primary ? `<span class="chip">primary</span>` : ""}
        <span class="spacer"></span><button data-act="msg-to" data-to="${esc(name)}">Message</button></h3>
      <div class="kv">
        <span>sessions</span><span>${sess.length ? sess.slice(0, 4).map(s => sessionLine(s, runOf(s, runs))).join("") + (sess.length > 4 ? `<div class="muted small">… ${sess.length - 4} older</div>` : "") : `<span class="muted">none in the last 12 h</span>`}</span>
        <span>doing</span><span>${doing}</span>
        <span>heartbeat</span><span>${ago(beat)}</span>
        <span>tasks</span><span>${held.map(t => `<a class="ref" href="#" data-ref="${t.ref}">${t.ref}</a> <span class="muted small">${t.status}</span>`).join(", ") || (task_ ? `<a class="ref" href="#" data-ref="${task_.ref}">${task_.ref}</a>` : `<span class="muted">none</span>`)}</span>
        <span>held by</span><span>${holdLine(l)}</span>
        <span>new session</span><span>${routeLine(l)}</span>
        <span>checkout</span><span class="files">${esc(reg.path || "")} · port ${esc(reg.port || "")}</span>
        <span>branch</span><span>${esc(l.branch || "?")}${l.ahead ? ` · <b style="color:var(--warn)">${l.ahead} unpushed</b>` : ""}${l.behind ? ` · ${l.behind} behind` : ""}</span>
        <span>HEAD</span><span class="small">${esc(l.head || "…")}</span>
        <span>tree</span><span>${l.modified === undefined ? "…" : files.length ? `${files.length} changed<div class="files">${files.slice(0, 10).map(esc).join("<br>")}${files.length > 10 ? `<br>… ${files.length - 10} more` : ""}</div>` : "clean"}</span>
        <span>GPU</span><span>${(l.gpu || []).length ? l.gpu.map(g => `<div class="files">${g.bench ? "<b>bench</b> " : ""}PID ${g.pid}: ${esc(g.cmd)}</div>`).join("") : `<span class="muted">none</span>`}</span>
      </div></div>`;
  }).join("");
  const other = ((S.state.live && S.state.live.other_gpu) || []).map(g => `<div class="files">PID ${g.pid}: ${esc(g.cmd)}</div>`).join("");
  const at = S.state.live && S.state.live.at;
  return `<div class="agents">${cards}</div>
    ${other ? `<h2>Other ${esc(project().process_label)}</h2><div class="card">${other}</div>` : ""}
    <p class="muted small">Tree and GPU state refresh every ~15 s${at ? ` (last ${ago(at)})` : " (loading…)"}. Claude sessions come from their hooks; Codex threads from Codex's own records (its thread list and each thread's log), so a Codex turn shows as working until it ends.</p>`;
}

// ------------------------------------------------------------------ drawer

// ---------------------------------------------------------------- transcripts
// A managed task's runs read as a Claude Code session (manager_transcript.py): the
// manager's prompt, the worker's thinking, what it said, every tool call with what came
// back. #log/T43 is every run of T43, #log/G12 a goal's planner runs, #log/RUNID one run.
// It renders once and then appends, so polling never moves the reader's scroll.
const LOG = { ref: null, meta: null, runs: {}, timer: null, metaAt: 0 };

function logLink(ref, label = "transcript") {
  return `<a class="small loglink" href="#log/${esc(ref)}" title="the whole session: prompt, thinking, messages, tool calls">${label}</a>`;
}

function logRef(r) { return r.task ? `T${r.task}` : r.goal ? `G${r.goal}` : r.run || r.id; }

function logItem(i) {
  switch (i.k) {
    case "think": return `<div class="li think"><span class="lab">thinking</span><div>${esc(i.text)}</div></div>`;
    case "say": return `<div class="li say">${md(i.text)}</div>`;
    case "user": return `<details class="li user"><summary><span class="lab">message to the worker</span> ${esc(i.text.slice(0, 140))}</summary><pre>${esc(i.text)}</pre></details>`;
    case "note": return `<div class="li note">${esc(i.text)}</div>`;
    case "tool": return `<details class="li tool" data-tool="${esc(i.id || "")}"><summary><b>${esc(i.name)}</b> <span>${esc(i.summary)}</span> <span class="st muted">…</span></summary>
      <pre class="in">${esc(i.input)}${i.more ? `\n… ${i.more - i.input.length} more characters` : ""}</pre><pre class="out muted">no result yet</pre></details>`;
    case "result": return `<details class="li tool"><summary><b>result</b></summary><pre class="out">${esc(i.text)}</pre></details>`;
    case "end": return `<div class="li end ${i.ok ? "ok" : "bad"}"><b>${i.ok ? "the run returned" : `the run ended: ${esc(i.subtype)}`}</b>
      ${i.turns ? `<span class="muted small">${i.turns} turns · ${mins(i.seconds)}</span>` : ""}${i.text ? `<details><summary class="small">what it returned</summary><pre>${esc(i.text)}</pre></details>` : ""}</div>`;
  }
  return "";
}

function logRun(r, n, all) {
  const live = r.state === "running" || r.state === "queued";
  return `<section class="logrun" id="run-${esc(r.id)}">
    <div class="loghead row"><b>Run ${n} of ${all}</b> <span class="muted">${esc(r.role)}</span> ${modelChip(r.model_label)}${r.agent ? `<span class="chip">${esc(r.agent)}</span>` : ""}
      <span class="state ${live ? "working" : "idle"}">${esc(r.state)}</span><span class="spacer"></span>
      <span class="muted small">started ${clock(r.started)} · ${live ? `running ${since(r.started)}, last step ${ago(r.beat)}` : `last step ${ago(r.beat)}`}</span></div>
    ${r.spent ? `<div class="spend">${esc(r.spent)}</div>` : ""}
    ${r.prompt ? `<details class="li user"><summary><span class="lab">the manager's prompt</span> <span class="muted small">${r.prompt.length.toLocaleString()} characters</span></summary><pre>${esc(r.prompt)}</pre></details>` : ""}
    <div class="logitems"></div>${r.error ? `<div class="li end bad"><b>error</b><pre>${esc(r.error)}</pre></div>` : ""}</section>`;
}

function logTop(meta) {
  const last = [...meta.runs].reverse().find(r => r.session);
  const resume = last ? `cd "${last.cwd}"; claude --resume ${last.session} --fork-session` : "";
  const ref = meta.ref;
  return `<div class="logbar card"><div class="row">
      ${/^T\d+$/.test(ref) ? `<a class="ref" href="#" data-ref="${ref}">${ref}</a>` : `<b>${esc(ref.length > 12 ? "run " + ref.slice(0, 8) : ref)}</b>`}
      <b>${esc(meta.title || "")}</b><span class="spacer"></span><a class="small" href="#manager">Manager tab</a></div>
    <div class="row small"><span class="muted">${meta.runs.length} run${meta.runs.length === 1 ? "" : "s"}</span>
      <label><input type="checkbox" id="log-tools"> hide tool calls</label>
      <label><input type="checkbox" id="log-open"> expand tool calls</label>
      <span class="spacer"></span><button class="link" data-act="log-end">jump to the latest</button></div>
    ${resume ? `<div class="row small"><span class="muted">open it in Claude Code (a fork: the worker is not disturbed)</span>
      <code class="files" id="log-resume">${esc(resume)}</code><button class="link" data-act="log-copy">copy</button></div>` : ""}</div>`;
}

function viewLog() { return `<div class="log" id="log"><p class="muted">Loading the transcript…</p></div>`; }

function logAtEnd() { return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120; }

async function logPull(r) {
  const st = LOG.runs[r.id] || (LOG.runs[r.id] = { next: 0 });
  const sec = document.getElementById(`run-${r.id}`);
  if (!sec) return;
  const j = await (await fetch(`/api/transcript?run=${r.id}&from=${st.next}`)).json();
  if (LOG.ref !== LOG.meta?.ref || !j.items) return;
  st.next = j.next;
  const box = sec.querySelector(".logitems"), open = $("#log-open")?.checked;
  for (const i of j.items) {
    if (i.k === "result") {
      const t = i.id && box.querySelector(`[data-tool="${CSS.escape(i.id)}"]`);
      if (t) {
        const out = t.querySelector("pre.out");
        out.className = "out" + (i.error ? " bad" : "");
        out.textContent = i.text + (i.more ? `\n… ${i.more - i.text.length} more characters` : "");
        t.querySelector(".st").textContent = i.error ? "error" : "";
        continue;
      }
    }
    if (i.k === "tool") {
      // consecutive tool calls fold into one line, so what it said and thought reads as one thread
      let run = box.lastElementChild;
      if (!run || !run.classList.contains("toolrun")) {
        box.insertAdjacentHTML("beforeend", `<details class="li toolrun"><summary></summary></details>`);
        run = box.lastElementChild;
        run.open = !!open;
        run.names = [];
      }
      run.insertAdjacentHTML("beforeend", logItem(i));
      if (open) run.lastElementChild.open = true;
      run.names.push((i.summary || i.name).slice(0, 70));
      const n = run.names.length;
      run.firstElementChild.innerHTML = `<b>${n} tool call${n === 1 ? "" : "s"}</b> <span>${esc(run.names.slice(-3).join(" · "))}</span>`;
      continue;
    }
    box.insertAdjacentHTML("beforeend", logItem(i));
  }
}

async function openLog(ref) {
  clearTimeout(LOG.timer);
  Object.assign(LOG, { ref, meta: null, runs: {} });
  $("#view").innerHTML = viewLog();
  const meta = await (await fetch(`/api/transcript?ref=${encodeURIComponent(ref)}`)).json();
  if (LOG.ref !== ref) return;
  if (meta.error || !meta.runs) { $("#log").innerHTML = `<p class="muted">${esc(meta.error || "No such transcript.")}</p>`; return; }
  LOG.meta = meta; LOG.metaAt = Date.now();
  $("#log").innerHTML = logTop(meta) + (meta.runs.map((r, n) => logRun(r, n + 1, meta.runs.length)).join("") || `<p class="muted">No runs yet.</p>`);
  for (const r of meta.runs) await logPull(r);
  window.scrollTo(0, document.documentElement.scrollHeight);
  LOG.timer = setTimeout(logTick, 3000);
}

async function logTick() {
  const ref = LOG.ref;
  if (S.view !== "log" || !LOG.meta) return;
  try {
    const end = logAtEnd();
    if (Date.now() - LOG.metaAt > 10000) {  // a new run of the task, a run that ended
      const meta = await (await fetch(`/api/transcript?ref=${encodeURIComponent(ref)}`)).json();
      if (LOG.ref !== ref || !meta.runs) return;
      LOG.metaAt = Date.now();
      meta.runs.forEach((r, n) => {
        const sec = document.getElementById(`run-${r.id}`);
        const head = logRun(r, n + 1, meta.runs.length);
        if (!sec) $("#log").insertAdjacentHTML("beforeend", head);
        else sec.querySelector(".loghead").outerHTML = new DOMParser().parseFromString(head, "text/html").querySelector(".loghead").outerHTML;
      });
      LOG.meta = meta;
    }
    for (const r of LOG.meta.runs) if (r.state === "running" || r.state === "queued" || !LOG.runs[r.id]) await logPull(r);
    if (end) window.scrollTo(0, document.documentElement.scrollHeight);
  } catch (e) { /* server restarting */ }
  if (LOG.ref === ref && S.view === "log") LOG.timer = setTimeout(logTick, 3000);
}

function openDrawer(d) { S.drawer = d; render(); markDrawerRead(); }
function closeDrawer() { S.drawer = null; render(); }

function markDrawerRead() {
  const d = S.drawer;
  if (!d || !S.state) return;
  let ids = [];
  if (d.type === "task") ids = S.state.messages.filter(m => m.topic === `T${d.id}` && m.unread).map(m => m.id);
  if (d.type === "thread") ids = S.state.messages.filter(m => m.thread === d.id && m.unread).map(m => m.id);
  if (ids.length) api("/api/read", { ids });
}

function renderDrawer() {
  const el = $("#drawer"), scrim = $("#scrim");
  const d = S.drawer;
  let html = "";
  if (d && d.type === "task" && task(d.id)) html = drawerTask(task(d.id));
  else if (d && d.type === "thread" && S.state.messages.some(m => m.thread === d.id)) html = drawerThread(d.id);
  else if (d && d.type === "new-task") html = drawerNewTask(d);
  else if (d && d.type === "new-msg") html = drawerNewMsg(d);
  el.hidden = scrim.hidden = !html;
  el.innerHTML = html;
}

function sel(name, options, value, attrs = "") {
  return `<select name="${name}" ${attrs}>${options.map(o => {
    const [v, l] = Array.isArray(o) ? o : [o, LABEL[o] || o];
    return `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`;
  }).join("")}</select>`;
}

const PRIOS = [[0, "P0 now"], [1, "P1 next"], [2, "P2 normal"], [3, "P3 someday"]];

// D212: the documents a managed task wrote (a plan, a spec), read from the task itself
function docsOf(id) {
  if (!(id in S.docs)) {
    S.docs[id] = null;
    fetch(`/api/task-docs?task=${id}`).then(r => r.json()).then(j => { S.docs[id] = j.docs || []; render(); })
      .catch(() => { S.docs[id] = []; });
  }
  return S.docs[id];
}

function docTitle(d) {
  const h = /^#\s+(.+)$/m.exec(d.text);
  return h ? h[1].trim() : d.path.split("/").pop();
}

function docHref(id, path) { return `#doc/T${id}/${path}`; }

function taskDocs(t) {
  const docs = docsOf(t.id);
  if (!docs || !docs.length) return "";
  return `<div class="docs"><label>documents to read (${docs.length})</label>${docs.map(d => `<div class="doc-row">
    <a href="${docHref(t.id, d.path)}"><b>${esc(docTitle(d))}</b></a>
    <span class="files">${esc(d.path)} @ ${esc(d.commit.slice(0, 8))} · ${d.text.split("\n").length} lines</span></div>`).join("")}</div>`;
}

function viewDoc() {
  const { id, path } = S.reading || {};
  const docs = id ? docsOf(id) : [];
  const d = (docs || []).find(x => x.path === path);
  const t = id && task(id);
  // the task opens over the document, so a reply can be written beside it
  const back = `<a class="ref" href="#" data-ref="T${id}">${t ? `${esc(t.ref)} ${esc(t.title)}` : `T${id}`}</a> <span class="muted small">(open the task: accept or reply)</span>`;
  if (!docs) return `<p>${back}</p><p class="muted">Loading…</p>`;
  if (!d) return `<p>${back}</p><p class="muted">${esc(path || "")} is not one of this task's documents.</p>`;
  return `<div class="reader"><p class="row">${back}<span class="spacer"></span>
    <span class="files">${esc(d.path)} @ ${esc(d.commit.slice(0, 12))}</span></p><div class="card">${md(d.text)}</div></div>`;
}

// D218: a managed task's model, why, and what it has spent; Yotam may pin a model
function drawerModel(t) {
  const m = taskModel(t.id);
  if (!m) return "";
  const pick = m.owner_tier || "auto";
  const by = Object.entries(m.models || {}).map(([k, v]) => `${esc(k)} ${tokens(v.tokens - v.output)} in / ${tokens(v.output)} out`).join(" · ");
  return `<div class="card"><div class="row"><b>model</b> ${modelChip(m.label || m.next, m.label ? m.ran_why : m.why, m.escalations > 0)}
      <span class="muted small">${esc(m.category)} work${m.escalations ? ` · escalated from Sonnet ${m.escalations}×` : ""}</span>
      <span class="spacer"></span>${sel("model", [["auto", "auto (policy)"], ...Object.entries(tierLabels())], pick, `data-model-task="${t.id}"`)}</div>
    <div class="small">${m.label && m.next !== m.label ? `ran on ${esc(m.label)}${m.ran_why ? ` (${esc(m.ran_why)})` : ""}; ` : ""}
      ${m.running ? "a further run" : "its next run"}: <b>${esc(m.next)}</b>, ${esc(m.why)}</div>
    ${spendLine(m)}${by ? `<div class="spend files">${by}</div>` : ""}
    <div class="small">${logLink(`T${t.id}`, "Open the transcript")} <span class="muted">every run: the prompt, its thinking, what it said, each tool call</span></div></div>`;
}

function drawerTask(t) {
  const st = S.state;
  const disc = st.messages.filter(m => m.topic === t.ref);
  const editing = S.editing[t.ref];
  const defTo = t.claimed_by || (t.assignee !== "any" && t.assignee !== "owner" ? t.assignee : "all");
  const from = t.note_id ? ` · from <a class="ref" href="#" data-ref="N${t.note_id}">N${t.note_id}</a>` : "";
  return `<div class="head"><span class="ref">${t.ref}</span><h1>${esc(t.title)}</h1><button data-act="close">✕</button></div>
    <div class="grid3">
      <div><label>status</label>${sel("status", st.statuses, t.status, `data-field="status" data-id="${t.id}"`)}</div>
      <div><label>priority</label>${sel("priority", PRIOS, t.priority, `data-field="priority" data-id="${t.id}"`)}</div>
      <div><label>assignee</label>${sel("assignee", ["any", ...st.people], t.assignee, `data-field="assignee" data-id="${t.id}"`)}</div>
    </div>
    <p class="muted small">created by ${esc(t.created_by)} ${ago(t.created)}${from}${t.claimed_by ? ` · held by <b>${esc(t.claimed_by)}</b>` : ""} · updated ${ago(t.updated)}</p>
    ${dependencyDetails(t)}
    ${drawerModel(t)}
    ${taskDocs(t)}
    ${t.status === "review" ? `<div class="row"><button class="primary" data-act="accept" data-id="${t.id}">Accept → done</button>
      <span class="muted small">or write what is missing below and</span><button data-act="send-back" data-id="${t.id}">Send back</button></div>` : ""}
    <label>description <button class="link" data-act="${editing ? "cancel-edit" : "edit"}" data-key="${t.ref}">${editing ? "cancel" : "edit"}</button>
      ${editing ? "" : `<button class="link" data-act="task-image" data-id="${t.id}" title="or paste a screenshot (Ctrl+V) while this task is open, or drop one here">+ image</button>`}</label>
    ${editing ? `<input class="wide" data-draft="edit-${t.ref}-title" value="${esc(t.title)}">
      <textarea data-draft="edit-${t.ref}-body" style="min-height:220px;margin-top:6px">${esc(t.body)}</textarea>
      <div class="row"><button class="primary" data-act="save-task" data-id="${t.id}">Save</button>${attachBtn(`edit-${t.ref}-body`)}</div>`
      : (t.body ? md(t.body) : `<div class="empty">No description. Goal, acceptance criteria, which evidence counts (Definition of done). Paste or drop a screenshot to show a rendering issue.</div>`)}
    <div class="grid3" style="grid-template-columns: 1fr 1fr">
      <div><label>depends on</label><input class="wide" data-draft="deps-${t.ref}" value="${esc(t.depends.map(d => "T" + d).join(", "))}"
        data-commit="depends" data-id="${t.id}" placeholder="T3, T4"></div>
      <div><label>links (commits, V/D entries)</label><input class="wide" data-draft="links-${t.ref}" value="${esc(t.links.join(" "))}"
        data-commit="links" data-id="${t.id}" placeholder="V98 D116 abc1234"></div>
    </div>
    <label>discussion (${disc.length}) <span class="muted small">newest first</span></label>
    <div class="composer">
      <div class="row">to ${sel("to", ["all", ...agents()], defTo, `data-draft-sel="task-to-${t.id}"`)}
        kind ${sel("kind", ["fyi", "question", "review", "handoff", "blocker"], "fyi", `data-draft-sel="task-kind-${t.id}"`)}</div>
      <textarea data-draft="task-msg-${t.id}" placeholder="Say something about ${t.ref}…  (Ctrl+Enter sends)"></textarea>
      <div class="row"><button class="primary" data-act="task-msg" data-id="${t.id}">Send</button>${attachBtn(`task-msg-${t.id}`)}</div>
    </div>
    ${[...disc].reverse().map(m => `<div class="msg"><div class="meta"><span class="kind ${esc(m.kind)}">${esc(m.kind)}</span><b>${esc(m.sender)}</b> → ${esc(m.recipient)}
      <span class="spacer"></span>${ago(m.created)} <a class="ref" href="#" data-ref="M${m.id}">M${m.id}</a></div>
      ${m.kind !== "reply" ? `<div class="subject">${esc(m.subject)}</div>` : ""}${m.body ? md(m.body) : ""}</div>`).join("") || `<div class="empty">No messages on this task yet.</div>`}`;
}

function drawerThread(tid) {
  const ms = S.state.messages.filter(m => m.thread === tid);
  const root = ms[0], to = counterpart(ms);
  return `<div class="head"><h1>${esc(root.subject)}</h1><button data-act="close">✕</button></div>
    <p class="muted small">thread M${tid}${root.topic ? ` · about <a class="ref" href="#" data-ref="${esc(root.topic)}">${esc(root.topic)}</a>` : ""}</p>
    ${ms.map(m => `<div class="msg"><div class="meta"><span class="kind ${esc(m.kind)}">${esc(m.kind)}</span><b>${esc(m.sender)}</b> → ${esc(m.recipient)}
      <span class="spacer"></span><span title="${esc(new Date(m.created * 1000).toLocaleString())}">${clock(m.created)}</span>
      <a class="ref" href="#" data-ref="M${m.id}">M${m.id}</a></div>${m.body ? md(m.body) : `<div class="muted small">(no body)</div>`}</div>`).join("")}
    <div class="composer">
      <div class="row">reply to ${sel("to", ["all", ...agents()], to, `data-draft-sel="thread-to-${tid}"`)}</div>
      <textarea data-draft="thread-${tid}" placeholder="Reply…  (Ctrl+Enter sends)"></textarea>
      <div class="row"><button class="primary" data-act="thread-reply" data-id="${tid}">Send</button>${attachBtn(`thread-${tid}`)}</div>
    </div>`;
}

function drawerNewTask(d) {
  const n = d.note_id ? note(d.note_id) : null;
  const k = "new-task";
  if (n && !(k + "-title" in S.drafts)) {
    const first = n.body.split("\n")[0].replace(/^#+\s*/, "");
    S.drafts[k + "-title"] = first.length > 90 ? first.slice(0, 89) + "…" : first;
    S.drafts[k + "-body"] = n.body;
  }
  return `<div class="head"><h1>New task${n ? ` from N${n.id}` : ""}</h1><button data-act="close">✕</button></div>
    <label>title</label><input class="wide" data-draft="${k}-title" placeholder="What should be true when this is done">
    <label>description</label>
    <textarea data-draft="${k}-body" style="min-height:220px" placeholder="Goal&#10;&#10;Acceptance:&#10;- validated through the player's input path (drag / fire)&#10;- shot + audits quoted&#10;- bench within budget"></textarea>
    <div class="grid3">
      <div><label>priority</label>${sel("priority", PRIOS, 2, `data-draft-sel="${k}-prio"`)}</div>
      <div><label>assignee</label>${sel("assignee", ["any", ...S.state.people], d.assignee || "any", `data-draft-sel="${k}-to"`)}</div>
      <div><label>starts as</label>${sel("status", [["idea", "idea (not yet)"], ["ready", "ready (agents may take it)"]], "idea", `data-draft-sel="${k}-status"`)}</div>
    </div>
    <label>depends on</label><input class="wide" data-draft="${k}-deps" placeholder="T3, T4">
    <div class="row" style="margin-top:14px"><button class="primary" data-act="create-task" data-note="${n ? n.id : ""}">Create task</button>${attachBtn(`${k}-body`)}</div>`;
}

function drawerNewMsg(d) {
  const k = "new-msg";
  return `<div class="head"><h1>New message</h1><button data-act="close">✕</button></div>
    <div class="grid3">
      <div><label>to</label>${sel("to", ["all", ...agents()], d.to || "all", `data-draft-sel="${k}-to"`)}</div>
      <div><label>kind</label>${sel("kind", ["fyi", "question", "handoff", "review", "blocker"], "fyi", `data-draft-sel="${k}-kind"`)}</div>
      <div><label>about (optional)</label><input class="wide" data-draft="${k}-topic" placeholder="T12 or N3" value="${esc(d.topic || "")}"></div>
    </div>
    <label>subject</label><input class="wide" data-draft="${k}-subject">
    <label>message</label><textarea data-draft="${k}-body" style="min-height:180px"></textarea>
    <div class="row" style="margin-top:10px"><button class="primary" data-act="send-msg">Send</button>${attachBtn(`${k}-body`)}</div>`;
}

// ------------------------------------------------------------------ images

function attachBtn(key) {
  return `<button class="link attach" data-act="attach" data-for="${esc(key)}" title="Attach an image (or paste / drop one into the text box)">+ image</button>`;
}

const imageFiles = dt => [...((dt && dt.files) || [])].filter(f => /^image\/(png|jpeg|gif|webp)$/.test(f.type));

async function uploadImage(file) {
  const r = await fetch("/api/upload", {
    method: "POST", body: file,
    headers: { "X-Board": "1", "X-Filename": encodeURIComponent(file.name || "screenshot.png"), "Content-Type": file.type },
  });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j.error) { toast(j.error || `HTTP ${r.status}`, true); throw new Error(j.error); }
  return j.markdown;
}

async function uploadAll(files) {
  toast(files.length > 1 ? `uploading ${files.length} images…` : "uploading image…");
  const marks = [];
  for (const f of files) marks.push(await uploadImage(f));
  return marks;
}

// Put image markdown at the caret of a text box, on lines of its own, and keep the draft.
function insertImages(el, marks) {
  const one = el.tagName === "INPUT";
  const sep = one ? " " : "\n";
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
  const before = el.value.slice(0, a), after = el.value.slice(b);
  const text = (before && !before.endsWith(sep) ? sep : "") + marks.join(one ? " " : "\n\n") + (after && !after.startsWith(sep) ? sep : "");
  el.value = before + text + after;
  const caret = before.length + text.length;
  el.focus();
  try { el.setSelectionRange(caret, caret); } catch (e) { }
  if (el.dataset.draft) S.drafts[el.dataset.draft] = el.value;
  updatePreviews(el);
  toast(marks.length > 1 ? `${marks.length} images added` : "image added");
}

async function imagesIntoField(el, files) {
  try { insertImages(el, await uploadAll(files)); } catch (e) { }
}

// No text box: the images go straight into the open task's description.
async function imagesIntoTask(id, files) {
  const t = task(id);
  if (!t) return;
  try {
    const marks = await uploadAll(files);
    const body = (t.body.trim() ? t.body.replace(/\s+$/, "") + "\n\n" : "") + marks.join("\n\n");
    await api("/api/task/update", { id, body });
    toast(marks.length > 1 ? `${marks.length} images added to ${t.ref}` : `image added to ${t.ref}`);
  } catch (e) { }
}

function pickImages(onFiles) {
  const input = document.createElement("input");
  input.type = "file"; input.accept = "image/png,image/jpeg,image/gif,image/webp"; input.multiple = true;
  input.onchange = () => { const fs = imageFiles(input); if (fs.length) onFiles(fs); };
  input.click();
}

const isTextBox = el => el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && el.dataset.draft));

document.addEventListener("paste", e => {
  const files = imageFiles(e.clipboardData);
  if (!files.length) return;
  if (isTextBox(e.target)) { e.preventDefault(); imagesIntoField(e.target, files); return; }
  if (S.drawer && S.drawer.type === "task") { e.preventDefault(); imagesIntoTask(S.drawer.id, files); }
});

document.addEventListener("dragover", e => {
  if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
  e.preventDefault();
  const box = isTextBox(e.target) ? e.target : (S.drawer && S.drawer.type === "task" && e.target.closest("#drawer")) ? $("#drawer") : null;
  document.querySelectorAll(".dropping").forEach(x => x !== box && x.classList.remove("dropping"));
  if (box) box.classList.add("dropping");
  e.dataTransfer.dropEffect = box ? "copy" : "none";
});
document.addEventListener("dragleave", e => { if (e.target.classList) e.target.classList.remove("dropping"); });
document.addEventListener("drop", e => {
  const files = imageFiles(e.dataTransfer);
  document.querySelectorAll(".dropping").forEach(x => x.classList.remove("dropping"));
  if (!files.length) return;
  e.preventDefault();  // never let the browser navigate away to the file
  if (isTextBox(e.target)) imagesIntoField(e.target, files);
  else if (S.drawer && S.drawer.type === "task" && e.target.closest("#drawer")) imagesIntoTask(S.drawer.id, files);
});

// Under every text box, the images its text holds, as thumbnails: what will be
// sent, before it is sent. The x takes an image out of the text.
function updatePreviews(only) {
  const boxes = only ? [only] : document.querySelectorAll("textarea[data-draft]");
  for (const box of boxes) {
    if (box.tagName !== "TEXTAREA") continue;
    let strip = box.nextElementSibling;
    if (!strip || !strip.classList.contains("previews")) {
      strip = document.createElement("div");
      strip.className = "previews";
      box.after(strip);
    }
    const imgs = [...box.value.matchAll(IMG_RE)].map(m => ({ md: m[0], alt: m[1], src: m[2] }));
    const key = imgs.map(i => i.md).join("|");
    if (strip.dataset.key === key) continue;
    strip.dataset.key = key;
    strip.hidden = !imgs.length;
    strip.innerHTML = imgs.map((i, n) => `<figure><img class="shot" src="${i.src}" alt="${esc(i.alt)}" title="${esc(i.alt)} (click to enlarge)">
      <button type="button" class="rm" data-act="rm-image" data-n="${n}" title="Remove this image">✕</button>
      <figcaption>${esc(i.alt)}</figcaption></figure>`).join("");
  }
}

function removeImage(btn) {
  const box = btn.closest(".previews").previousElementSibling;
  const n = +btn.dataset.n;
  let i = 0;
  box.value = box.value.replace(IMG_RE, m => (i++ === n ? "" : m))
    .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "").replace(/\s+$/, "");
  if (box.dataset.draft) S.drafts[box.dataset.draft] = box.value;
  updatePreviews(box);
}

// the lightbox: click an image to see it whole, click or Esc to close
function showImage(src, alt) {
  const lb = $("#lightbox");
  lb.innerHTML = `<img src="${src}" alt="${esc(alt)}"><div class="cap">${esc(alt)} · <a href="${src}" target="_blank" rel="noopener">open original</a></div>`;
  lb.hidden = false;
}

// ------------------------------------------------------------------ actions

function draft(k) { return (S.drafts[k] || "").trim(); }
function clear(...ks) { ks.forEach(k => delete S.drafts[k]); }
function selVal(k, fallback) { return k in S.drafts ? S.drafts[k] : fallback; }

function openRef(r) {
  const kind = r[0], id = +r.slice(1);
  if (kind === "T" && task(id)) openDrawer({ type: "task", id });
  else if (kind === "M" && msg(id)) openDrawer({ type: "thread", id: msg(id).thread });
  else if (kind === "N" && note(id)) {
    S.drawer = null; S.noteFilter = "all"; S.tag = null; location.hash = "thoughts";
    setTimeout(() => { const el = document.getElementById(r); if (el) { el.scrollIntoView({ block: "center" }); el.style.outline = "2px solid var(--accent)"; } }, 50);
  } else toast(`${r} not found`, true);
}

const ACTS = {
  "log-end": () => window.scrollTo(0, document.documentElement.scrollHeight),
  "mobile-build": async () => { await api("/api/mobile-build", {}); toast("TestFlight build queued"); },
  "log-copy": async () => { try { await navigator.clipboard.writeText($("#log-resume").textContent); toast("Copied"); } catch (e) { toast("Copy failed", true); } },
  "manager-control": el => api("/api/manager", { action: el.dataset.command }),
  close: () => closeDrawer(),
  attach: el => {
    const key = el.dataset.for;
    pickImages(files => {
      const box = document.querySelector(`[data-draft="${CSS.escape(key)}"]`);
      if (box) imagesIntoField(box, files);
    });
  },
  "task-image": el => pickImages(files => imagesIntoTask(+el.dataset.id, files)),
  "rm-image": el => removeImage(el),
  "new-task": () => openDrawer({ type: "new-task" }),
  "new-msg": () => openDrawer({ type: "new-msg" }),
  "msg-to": el => openDrawer({ type: "new-msg", to: el.dataset.to }),
  "open-task": el => openDrawer({ type: "task", id: +el.dataset.id }),
  "open-thread": el => openDrawer({ type: "thread", id: +el.dataset.id }),
  read: el => api("/api/read", { ids: [+el.dataset.id] }),
  reply: async el => {
    const id = +el.dataset.id, body = draft(`reply-${id}`);
    if (!body) return toast("write an answer first", true);
    await api("/api/msg", { reply_to: id, body });
    clear(`reply-${id}`); toast("sent");
  },
  accept: async el => { await api("/api/task/update", { id: +el.dataset.id, status: "done" }); toast("done"); },
  "send-back": async el => {
    const t = task(+el.dataset.id), body = draft(`task-msg-${t.id}`);
    if (!body) return toast("say what is missing in the message box first", true);
    const to = t.claimed_by || "all";
    const last = S.state.messages.filter(m => m.topic === t.ref).pop();
    await api("/api/msg", { to, subject: `${t.ref}: ${t.title}`, body, kind: "review", topic: t.ref, reply_to: last ? last.id : null });
    await api("/api/task/update", { id: t.id, status: "in_progress" });
    clear(`task-msg-${t.id}`); toast(`sent back to ${to}`);
  },
  "save-thought": async () => {
    const body = draft("thought");
    if (!body) return toast("write something first", true);
    await api("/api/note", { body, tags: draft("thought-tags") });
    clear("thought", "thought-tags"); toast("saved");
  },
  "note-filter": el => { S.noteFilter = el.dataset.f; render(); },
  tag: el => { S.tag = S.tag === el.dataset.tag ? null : el.dataset.tag; render(); },
  edit: el => { S.editing[el.dataset.key] = true; render(); },
  "cancel-edit": el => { const k = el.dataset.key; delete S.editing[k]; clear(`edit-${k}`, `edit-${k}-tags`, `edit-${k}-title`, `edit-${k}-body`); render(); },
  "save-note": async el => {
    const id = +el.dataset.id, k = `edit-N${id}`;
    const n = note(id);
    await api("/api/note/update", { id, body: selVal(k, n.body), tags: selVal(k + "-tags", n.tags) });
    delete S.editing["N" + id]; clear(k, k + "-tags"); render();
  },
  "note-status": el => api("/api/note/update", { id: +el.dataset.id, status: el.dataset.s }),
  "note-to-task": el => { clear("new-task-title", "new-task-body"); openDrawer({ type: "new-task", note_id: +el.dataset.id }); },
  "ask-about": el => openDrawer({ type: "new-msg", topic: `N${el.dataset.id}` }),
  "create-task": async el => {
    const k = "new-task", title = draft(k + "-title");
    if (!title) return toast("a task needs a title", true);
    const r = await api("/api/task", {
      title, body: S.drafts[k + "-body"] || "", priority: +selVal(k + "-prio", 2),
      assignee: selVal(k + "-to", "any"), status: selVal(k + "-status", "idea"),
      depends: draft(k + "-deps"), note_id: el.dataset.note ? +el.dataset.note : null,
    });
    clear(k + "-title", k + "-body", k + "-prio", k + "-to", k + "-status", k + "-deps");
    openDrawer({ type: "task", id: r.id }); toast(`T${r.id} created`);
  },
  "save-task": async el => {
    const t = task(+el.dataset.id), k = `edit-${t.ref}`;
    await api("/api/task/update", { id: t.id, title: selVal(k + "-title", t.title), body: selVal(k + "-body", t.body) });
    delete S.editing[t.ref]; clear(k + "-title", k + "-body"); render();
  },
  "task-msg": async el => {
    const t = task(+el.dataset.id), body = draft(`task-msg-${t.id}`);
    if (!body) return toast("write something first", true);
    const defTo = t.claimed_by || (t.assignee !== "any" && t.assignee !== "owner" ? t.assignee : "all");
    const last = S.state.messages.filter(m => m.topic === t.ref).pop();
    await api("/api/msg", {
      to: selVal(`task-to-${t.id}`, defTo), kind: selVal(`task-kind-${t.id}`, "fyi"),
      subject: `${t.ref}: ${t.title}`, body, topic: t.ref, reply_to: last ? last.id : null,
    });
    clear(`task-msg-${t.id}`); toast("sent");
  },
  "thread-reply": async el => {
    const tid = +el.dataset.id, body = draft(`thread-${tid}`);
    if (!body) return toast("write something first", true);
    const ms = S.state.messages.filter(m => m.thread === tid);
    await api("/api/msg", { reply_to: ms[ms.length - 1].id, to: selVal(`thread-to-${tid}`, counterpart(ms)), body });
    clear(`thread-${tid}`); toast("sent");
  },
  "send-msg": async () => {
    const k = "new-msg", subject = draft(k + "-subject");
    if (!subject) return toast("a message needs a subject", true);
    const r = await api("/api/msg", {
      to: selVal(k + "-to", S.drawer.to || "all"), kind: selVal(k + "-kind", "fyi"), subject,
      body: S.drafts[k + "-body"] || "", topic: draft(k + "-topic") || null,
    });
    clear(k + "-to", k + "-kind", k + "-subject", k + "-body", k + "-topic");
    openDrawer({ type: "thread", id: r.id }); toast("sent");
  },
};

document.addEventListener("click", e => {
  if (e.target.closest("#lightbox")) { if (!e.target.closest("a")) $("#lightbox").hidden = true; return; }
  const img = e.target.closest("img.shot");
  if (img) { e.preventDefault(); showImage(img.getAttribute("src"), img.alt); return; }
  const r = e.target.closest("[data-ref]");
  if (r) { e.preventDefault(); openRef(r.dataset.ref); return; }
  const a = e.target.closest("[data-act]");
  if (a && a.tagName !== "SELECT" && a.type !== "checkbox") {
    e.preventDefault();
    const f = ACTS[a.dataset.act];
    if (f) Promise.resolve(f(a)).catch(() => { });
    return;
  }
  const ot = e.target.closest("[data-open-task]");
  if (ot && !e.target.closest("button")) return openDrawer({ type: "task", id: +ot.dataset.openTask });
  const oh = e.target.closest("[data-open-thread]");
  if (oh) return openDrawer({ type: "thread", id: +oh.dataset.openThread });
  const tc = e.target.closest(".tcard");
  if (tc) return openDrawer({ type: "task", id: +tc.dataset.task });
  if (e.target.id === "scrim") closeDrawer();
});

document.addEventListener("input", e => {
  const k = e.target.dataset && e.target.dataset.draft;
  if (k) S.drafts[k] = e.target.value;
  if (k && e.target.tagName === "TEXTAREA") updatePreviews(e.target);
});

document.addEventListener("change", async e => {
  if (e.target.id === "log-tools") { $("#log").classList.toggle("notools", e.target.checked); return; }
  if (e.target.id === "log-open") { document.querySelectorAll("#log details.tool, #log details.toolrun").forEach(d => { d.open = e.target.checked; }); return; }
  const el = e.target;
  if (el.dataset.draftSel) { S.drafts[el.dataset.draftSel] = el.value; return; }
  if (el.dataset.modelTask) {
    await api("/api/manager", { action: "model", task: +el.dataset.modelTask, tier: el.value }).catch(() => { });
    return;
  }
  if (el.dataset.field) {
    const v = el.dataset.field === "priority" ? +el.value : el.value;
    await api("/api/task/update", { id: +el.dataset.id, [el.dataset.field]: v }).catch(() => { });
    return;
  }
  if (el.dataset.commit) {
    const field = el.dataset.commit, raw = el.value.trim();
    const value = field === "links" ? raw.split(/[\s,]+/).filter(Boolean) : raw;
    clear(el.dataset.draft);
    await api("/api/task/update", { id: +el.dataset.id, [field]: value }).catch(() => { });
    return;
  }
  if (el.dataset.act === "toggle-dropped") { S.showDropped = el.checked; render(); }
  if (el.dataset.act === "thread-who") { S.threadWho = el.value; render(); }
});

document.addEventListener("keydown", e => {
  const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.tagName === "TEXTAREA") {
    e.preventDefault();
    const btn = e.target.closest(".composer, .card, .drawer")?.querySelector("button.primary");
    if (btn) btn.click();
    return;
  }
  if (e.key === "Escape" && !$("#lightbox").hidden) { $("#lightbox").hidden = true; return; }
  if (e.key === "Escape" && S.drawer) { closeDrawer(); return; }
  if (!inField && e.key === "n") { e.preventDefault(); $("#capture-input").focus(); }
});

$("#capture").addEventListener("submit", async e => {
  e.preventDefault();
  const body = draft("quick");
  if (!body) return;
  await api("/api/note", { body }).catch(() => { });
  clear("quick"); $("#capture-input").value = ""; toast("thought saved");
});

// ------------------------------------------------------------------ drag and drop

function wirePan(board) {
  let pan = null;
  board.addEventListener("pointerdown", e => {
    if (e.button !== 0 || e.pointerType !== "mouse" || e.target.closest(".tcard, a, button, input, select, textarea")) return;
    // Leave the native scrollbars alone.
    const bounds = board.getBoundingClientRect();
    if (e.clientX >= bounds.left + board.clientWidth || e.clientY >= bounds.top + board.clientHeight) return;
    pan = { id: e.pointerId, x: e.clientX, y: e.clientY, left: board.scrollLeft, top: board.scrollTop };
    S.boardPanning = true;
    board.setPointerCapture(e.pointerId);
    board.classList.add("panning");
    e.preventDefault();
  });
  board.addEventListener("pointermove", e => {
    if (!pan || e.pointerId !== pan.id) return;
    board.scrollLeft = pan.left + pan.x - e.clientX;
    board.scrollTop = pan.top + pan.y - e.clientY;
  });
  const finish = e => {
    if (!pan || e.pointerId !== pan.id) return;
    const id = pan.id;
    pan = null;
    S.boardPanning = false;
    board.classList.remove("panning");
    if (board.hasPointerCapture(id)) board.releasePointerCapture(id);
    if (S.renderPending) render();
  };
  board.addEventListener("pointerup", finish);
  board.addEventListener("pointercancel", finish);
  board.addEventListener("lostpointercapture", finish);
}

function wireDrag() {
  let dragId = null;
  document.querySelectorAll(".tcard").forEach(c => {
    c.addEventListener("dragstart", e => {
      if (e.target.closest("a")) { e.preventDefault(); return; }
      dragId = +c.dataset.task; c.classList.add("dragging"); e.dataTransfer.setData("text/plain", String(dragId));
    });
    c.addEventListener("dragend", () => c.classList.remove("dragging"));
    c.addEventListener("dragover", e => { e.preventDefault(); c.classList.add("over"); });
    c.addEventListener("dragleave", () => c.classList.remove("over"));
  });
  document.querySelectorAll(".col").forEach(col => {
    col.addEventListener("dragover", e => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", e => { if (!col.contains(e.relatedTarget)) col.classList.remove("drop"); });
    col.addEventListener("drop", async e => {
      e.preventDefault();
      col.classList.remove("drop");
      document.querySelectorAll(".tcard.over").forEach(x => x.classList.remove("over"));
      const id = +e.dataTransfer.getData("text/plain") || dragId;
      const t = task(id);
      if (!t) return;
      const status = col.dataset.col;
      const target = e.target.closest(".tcard");
      const upd = { id };
      if (status !== t.status) upd.status = status;
      const siblings = S.state.tasks.filter(x => x.status === status && x.id !== id);
      if (target && +target.dataset.task !== id) {
        const tt = task(+target.dataset.task);
        const i = siblings.indexOf(tt);
        const prev = i > 0 ? siblings[i - 1] : null;
        upd.rank = prev && prev.priority === tt.priority ? (prev.rank + tt.rank) / 2 : tt.rank - 1;
        if (tt.priority !== t.priority) upd.priority = tt.priority;
      } else if (status !== t.status) {
        upd.rank = siblings.reduce((m, x) => Math.max(m, x.rank), 0) + 1;
      }
      if (Object.keys(upd).length > 1) await api("/api/task/update", upd).catch(() => { });
    });
  });
}

// ------------------------------------------------------------------ nightly (D262, D265)
// The bench and the cleanup crew: what runs now, and each night's report over time.

function loadNightly() {
  if (S.nightlyBusy) return;
  S.nightlyBusy = true;
  fetch("/api/nightly").then(r => r.json()).then(j => { S.nightly = j; S.nightlyAt = Date.now(); if (S.view === "nightly") render(); })
    .catch(() => { }).finally(() => { S.nightlyBusy = false; });
}

function spark(vals, title) {
  // oldest first; a missing value leaves a gap in the x axis, not a zero
  const pts = vals.map((v, i) => [i, v]).filter(p => p[1] != null);
  if (pts.length < 2) return `<span class="muted small">${pts.length ? "one night so far" : "no data"}</span>`;
  const w = 140, h = 30, ys = pts.map(p => p[1]), lo = Math.min(...ys), span = Math.max(...ys) - lo || 1, n = vals.length - 1 || 1;
  const xy = p => [(p[0] / n * (w - 4) + 2).toFixed(1), (h - 3 - (p[1] - lo) / span * (h - 6)).toFixed(1)];
  const d = pts.map((p, k) => `${k ? "L" : "M"}${xy(p).join(",")}`).join("");
  const last = xy(pts[pts.length - 1]);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>
    <path d="${d}" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="currentColor"/></svg>`;
}

function trend(label, rows, key, fmt) {
  const vals = rows.map(r => r[key]);
  const now = vals[vals.length - 1];
  return `<div class="trend"><span class="muted small">${esc(label)}</span>${spark(vals, label)}<b>${now == null ? "–" : fmt(now)}</b></div>`;
}

function benchStatus(n, live) {
  const r = n.report;
  if (r && r.offenders.length) return `<span class="pill bad">${new Set(r.offenders.map(o => o.commit)).size} commit(s) slower</span>`;
  if (r && (r.unrunnable || []).some(u => !u.at_head)) return `<span class="pill bad">a case no longer runs</span>`;
  if (r && r.note) return `<span class="pill">baseline measured</span>`;
  if (r) return `<span class="pill good">within budget</span>`;
  if (n.failed) return `<span class="pill bad" title="${esc(n.failed)}">failed</span>`;
  if (live) return `<span class="pill run">running</span>`;
  return `<span class="pill bad" title="it started but kept no report: the log says why">no report</span>`;
}

function fmtMs(v) { return v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)} ms`; }

function benchNight(n, live) {
  const r = n.report;
  const span = r ? `${esc((r.reference || "none").slice(0, 10))} → ${esc((r.head || "").slice(0, 10))}` : "";
  const head = `<summary class="row"><b>${esc(n.day)}</b> ${benchStatus(n, live)}
    <span class="muted small">${r ? `${r.commits.length} commit(s) · ${r.cases.length} case(s) · ${span}` : n.started ? `started ${clock(n.started)}` : ""}</span>
    ${r && r.worst_ms != null ? `<span class="small">worst ${fmtMs(r.worst_ms)}</span>` : ""}
    ${r && r.task ? `<a class="ref" href="#" data-ref="T${r.task}">T${r.task}</a>` : ""}</summary>`;
  if (!r) return `<details class="card night">${head}${n.failed ? `<pre class="log">${esc(n.failed)}</pre>` : `<p class="muted small">No report was kept; the bench's log below has the end of the last run.</p>`}</details>`;
  const verdicts = Object.entries(r.verdicts).map(([c, v]) => `<tr><td>${esc(c)}</td><td>${esc(v.outcome || "–")}</td>
    <td>${fmtMs(v.delta_ms && v.delta_ms.combined_ms)}</td><td>${fmtMs(v.delta_ms && v.delta_ms.gpu_ms)}</td></tr>`).join("");
  const found = r.offenders.map(o => `<tr><td><code>${esc(o.commit.slice(0, 10))}</code> ${esc(o.subject)}</td><td>${esc(o.case)}</td>
    <td>${fmtMs(o.delta_ms && o.delta_ms.combined_ms)}</td><td>${fmtMs(o.own_delta_ms && o.own_delta_ms.combined_ms)}${o.creep ? " (crossed a creep)" : ""}</td>
    <td>${esc((o.hard_failures || []).join(", "))}</td></tr>`).join("");
  return `<details class="card night"${r.offenders.length ? " open" : ""}>${head}
    ${r.note ? `<p class="muted">${esc(r.note)}</p>` : ""}
    ${found ? `<h3>What got slower</h3><table class="models"><tr><th>commit</th><th>case</th><th>from the last good</th><th>its own step</th><th>hard limits</th></tr>${found}</table>` : ""}
    ${(r.attributed || []).length ? `<h3>Where the rises within the allowance came from</h3><table class="models"><tr><th>case</th><th>commit</th><th>its own step</th><th>passes that grew</th></tr>${r.attributed.map(a => `<tr><td>${esc(a.case)}</td><td><code>${esc(a.commit.slice(0, 10))}</code> ${esc(a.subject)}</td>
      <td>${fmtMs(a.own_delta_ms && a.own_delta_ms.combined_ms)}${a.within_noise ? ' <span class="muted">(within noise)</span>' : ''}</td><td class="small">${(a.passes || []).map(w => `${esc(w.pass_name)} ${fmtMs(w.delta_ms)}`).join(', ') || '–'}</td></tr>`).join('')}</table>` : ""}
    ${verdicts ? `<h3>Head against the night before, per case</h3><table class="models"><tr><th>case</th><th>verdict</th><th>combined</th><th>GPU</th></tr>${verdicts}</table>` : ""}
    ${(r.unrunnable || []).length ? `<h3>Cases it could not compare</h3>${r.unrunnable.map(u => `<div class="small"><b>${esc(u.case)}</b>: ${u.at_head ? "new since the reference: measured at the head, compared from the next night" : `<span style="color:var(--accent)">does not run at the head</span> <span class="muted">${esc(u.why)}</span>`}</div>`).join("")}` : ""}
    <h3>Commits that changed the runtime</h3><div class="small">${r.commits.map(c => `<div><code>${esc(c.commit.slice(0, 10))}</code> ${esc(c.subject)}</div>`).join("") || `<span class="muted">none</span>`}</div>
  </details>`;
}

function benchLive(b) {
  if (!b.running) return `<div class="card"><p><b>Not running.</b> <span class="muted">The next night starts at 04:00${b.request ? `; the night of ${esc(b.request)} is queued and starts at the service's next tick` : ""}.</span></p></div>`;
  const h = b.hold, c = b.claim;
  let gpu = `<span class="muted">building, or between measurements</span>`;
  if (c && c.state === "waiting") gpu = `<span style="color:var(--warn)">waiting for the GPU ${ago(c.created).replace(" ago", "")}</span> for <i>${esc(c.note)}</i>${c.asked.length ? ` · asked ${esc(c.asked.join(", "))} to stash a game that cannot be frozen` : ""}`;
  else if (c && c.state === "running") gpu = `<span style="color:var(--ok)">measuring</span> <i>${esc(c.note)}</i> since ${clock(c.started)}`;
  return `<div class="card"><p class="row"><span class="pill run">benching</span> <span>since ${clock(h.since)} (${ago(h.since)})</span>
      <span class="muted small">pid ${h.pid} · ${h.measured} capture(s) done · ${h.suspended} worker process(es) paused now</span></p>
    <p>${gpu}</p></div>`;
}

function crewNight(s) {
  return `<details class="card night"><summary class="row"><b>${esc(s.day)}</b>
    <span class="small">penalty ${s.penalty ?? "–"} · duplicated ${s.dup_tokens ?? "–"} tokens · loop edges ${s.cycle_edges ?? "–"}
    · ${s.worse} function(s) worse · detours ${s.detour_share == null ? "–" : (s.detour_share * 100).toFixed(1) + "%"} · $${s.cost_of_pass ?? "–"} per accepted task</span></summary>
    <div class="md">${md(s.summary)}</div></details>`;
}

function crewTask(t) {
  const commits = t.commits.map(c => `<div class="small"><code>${esc(c.sha)}</code> ${esc(c.subject)}
    ${c.review ? `<span class="pill ${c.review === "keep" ? "good" : "bad"}">review: ${esc(c.review)}</span>` : t.crew === "code" ? `<span class="pill bad">no review</span>` : ""}
    ${c.trailer ? "" : `<span class="muted" title="no Crew: T${t.id} trailer: the scorecard does not credit it">(no crew trailer)</span>`}
    ${c.directive ? `<div class="muted">${esc(c.directive)}</div>` : ""}</div>`).join("") || `<div class="muted small">no commits</div>`;
  return `<tr><td><a class="ref" href="#" data-ref="T${t.id}">T${t.id}</a> ${logLink(`T${t.id}`)}</td><td>${esc(t.crew)}</td><td>${esc(t.day)}</td>
    <td>${esc(t.status)}${t.owner ? ` · ${esc(t.owner)}` : ""}</td><td>${commits}</td></tr>`;
}

function flaggedSection(rows) {
  if (!rows.length) return `<div class="card muted">Nothing flagged yet: no night has found a commit that costs performance.</div>`;
  return `<div class="card"><table class="models"><tr><th>night</th><th>commit</th><th>case</th><th>its own step</th><th>where</th><th></th></tr>
    ${rows.map(f => `<tr><td>${esc(f.day)}</td><td><code>${esc(f.commit)}</code> ${esc(f.subject)}</td><td>${esc(f.case)}</td>
      <td><b>${fmtMs(f.own_ms)}</b></td><td class="small">${f.passes.map(p => `${esc(p.pass_name)} ${fmtMs(p.delta_ms)}`).join(", ") || "–"}${f.hard.length ? ` · <span style="color:var(--accent)">${esc(f.hard.join(", "))}</span>` : ""}</td>
      <td>${f.kind === "over the allowance" ? `<span class="pill bad">over +0.5 ms</span>${f.task ? ` <a class="ref" href="#" data-ref="T${f.task}">T${f.task}</a>` : ""}` : `<span class="pill">within the allowance</span>`}</td></tr>`).join("")}</table></div>`;
}

function historySection(h) {
  if (!h.cases.length) return `<div class="card muted">No night has measured a case yet.</div>`;
  const n = h.points.length;
  const rows = h.cases.map(c => {
    const known = c.values.map((v, i) => [i, v]).filter(p => p[1] != null);
    const last = known.length ? known[known.length - 1] : null, prev = known.length > 1 ? known[known.length - 2] : null;
    const step = last && prev ? last[1] - prev[1] : null;
    return `<tr><td>${esc(c.case)}</td><td>${spark(c.values, c.case)}</td><td><b>${last ? last[1].toFixed(3) + " ms" : "–"}</b></td>
      <td>${step == null ? "–" : `<span style="color:${step > 0.05 ? "var(--accent)" : step < -0.05 ? "var(--ok)" : "inherit"}">${fmtMs(step)}</span>`}</td>
      <td class="small">${known.length}/${n}${c.failed.length ? ` · <span class="pill bad" title="${esc(c.failed.map(f => `${f.day}: ${f.why}`).join("\n"))}">failed ${c.failed.length}×</span>` : ""}</td></tr>`;
  }).join("");
  return `<div class="card"><p class="muted small">${h.points.map(p => `${esc(p.label)} <code>${esc(p.commit)}</code>`).join(" → ")}</p>
    <table class="models"><tr><th>case</th><th>combined ms per night</th><th>latest</th><th>since the night before</th><th>nights measured</th></tr>${rows}</table></div>`;
}

function rehearsalSection(r) {
  if (!r) return "";
  const head = `<span class="pill">rehearsal</span> started ${clock(r.started)}`;
  if (r.failed) return `<div class="card"><p class="row">${head} <span class="pill bad">failed</span></p><pre class="log">${esc(r.failed)}</pre></div>`;
  if (!r.report) return `<div class="card"><p class="row">${head} <span class="pill run">running or ended without a report</span></p></div>`;
  return benchNight({ day: "rehearsal of tonight", started: r.started, failed: null, report: r.report }, false);
}

function viewNightly() {
  const N = S.nightly;
  if (!N) { loadNightly(); return `<p class="muted">Loading…</p>`; }
  const b = N.bench, c = N.crew;
  const cards = c.nights.slice().reverse();
  const latest = b.nights.length ? b.nights[0].day : null;
  return `<h2>Nightly bench <span class="muted small">D262: main against the night before, from 04:00</span></h2>
    ${benchLive(b)}
    ${rehearsalSection(b.rehearsal)}
    <h3>Flagged as costing performance <span class="muted small">every commit a night found slower, with the passes that grew</span></h3>
    ${flaggedSection(b.flagged)}
    <h3>Each case over time <span class="muted small">its measured time at each night's head</span></h3>
    ${historySection(b.history)}
    <h3>The nights</h3>
    ${b.nights.map(n => benchNight(n, b.running && n.day === latest)).join("") || `<p class="muted">No night has run yet.</p>`}
    <details class="card"><summary>The bench's log <span class="muted small">bench-night.log, its end</span></summary><pre class="log">${esc(b.log.join("\n"))}</pre></details>

    <h2>Cleanup crew <span class="muted small">D265, D280: mode ${esc(c.mode)} · last night ${esc(c.night || "never")}</span></h2>
    ${c.running.length ? `<div class="card"><b>Working now:</b> ${c.running.map(t => `<a class="ref" href="#" data-ref="T${t.id}">T${t.id}</a> ${esc(t.crew)} (${esc(t.status)}) ${logLink(`T${t.id}`)}`).join(" · ")}</div>` : ""}
    <div class="card trends">${trend("debt penalty", cards, "penalty", v => v.toFixed(0))}
      ${trend("duplicated tokens", cards, "dup_tokens", v => v)}
      ${trend("module loop edges", cards, "cycle_edges", v => v)}
      ${trend("detour share of tokens", cards, "detour_share", v => (v * 100).toFixed(1) + "%")}
      ${trend("cost per accepted task", cards, "cost_of_pass", v => "$" + v)}</div>
    <h3>The crews' tasks</h3>
    <div class="card"><table class="models"><tr><th>task</th><th>crew</th><th>night</th><th>status</th><th>commits, review and directive</th></tr>
      ${c.tasks.map(crewTask).join("") || `<tr><td colspan="5" class="muted">No crew task yet.</td></tr>`}</table></div>
    <h3>Scorecards</h3>
    ${c.nights.map(crewNight).join("") || `<p class="muted">No scorecard yet.</p>`}
    <details class="card"><summary>The crew's log <span class="muted small">crew-night.log</span></summary><pre class="log">${esc(c.log.join("\n"))}</pre></details>
    <p class="muted small">Refreshed ${ago(N.now)}; this view updates every 5 s while it is open.</p>`;
}

// ------------------------------------------------------------------ start

function route() {
  const h = location.hash.slice(1);
  if (/^[TMN]\d+$/.test(h)) { S.view = h[0] === "N" ? "thoughts" : S.view; load().then(() => openRef(h)); return; }
  const doc = /^doc\/T(\d+)\/(.+)$/.exec(h);  // D212: #doc/T40/docs/plan.md, a task's document
  if (doc) { S.view = "doc"; S.reading = { id: +doc[1], path: decodeURIComponent(doc[2]) }; S.drawer = null; render(); return; }
  const lg = /^log\/([TG]\d+|[0-9a-f]{32})$/.exec(h);  // a managed run's transcript
  if (lg) { S.view = "log"; S.drawer = null; document.querySelectorAll("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.view === "manager")); openLog(lg[1]); return; }
  S.view = ["inbox", "board", "thoughts", "threads", "agents", "manager", "nightly"].includes(h) ? h : "inbox";
  if (S.view === "nightly") loadNightly();
  render();
}
window.addEventListener("hashchange", route);
route();
load();
setInterval(poll, 2000);
setInterval(load, 15000);
