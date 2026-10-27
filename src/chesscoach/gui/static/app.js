// chesscoach front end. All chess logic lives in Python; this file only
// shows positions, sends moves, and renders what the coach wrote.
import { Chessground } from "./vendor/chessground.min.js";

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

// ---------------------------------------------------------------- helpers

const FIGURINES = { K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘" };
const figurine = (san) => san.replace(/[KQRBN]/g, (p) => FIGURINES[p]);
// In sentences only the piece letters of moves become figurines ("Black" stays).
const figurineText = (text) => text.replace(
  /(^|[^A-Za-z])([KQRBN])(?=[a-h1-8]?x?[a-h][1-8])/g, (_, pre, p) => pre + FIGURINES[p]);

const PRAISE = new Set(["found the only good move", "punished the opponent's mistake",
                        "delivered checkmate"]);
function markOf(mv) {
  switch (mv.label) {
    case "Blunder": return "??";
    case "Mistake": return "?";
    case "Inaccuracy": return "?!";
    case "Excellent": return PRAISE.has(mv.reason) ? "!" : "";
    default: return "";
  }
}
const JUDGMENT_TEXT = {
  Blunder: "Blunder", Mistake: "Mistake", Inaccuracy: "Inaccuracy",
  Good: "Good move", Excellent: "Excellent", Book: "Opening theory",
};
const BAD = new Set(["Inaccuracy", "Mistake", "Blunder"]);

function winPctWhite(cp, mate) {
  if (mate !== null && mate !== undefined) return mate > 0 ? 100 : mate < 0 ? 0 : 50;
  if (cp === null || cp === undefined) return 50;
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sqs = (uci) => [uci.slice(0, 2), uci.slice(2, 4)];
const isCheck = (san) => /[+#]$/.test(san || "");

function toast(text, ms = 3200) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

async function call(name, ...args) {
  const res = await api()[name](...args);
  if (res && res.error) {
    toast(res.error, 5000);
    return null;
  }
  return res;
}

// ---------------------------------------------------------------- board

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const board = Chessground($("board"), {
  fen: START,
  coordinates: true,
  animation: { duration: 180 },
  movable: { free: false, color: undefined },
  premovable: { enabled: false },
  drawable: {
    enabled: true, visible: true,
    // the opponent's threat: a dashed arrow (see .cg-shapes rule in app.css)
    brushes: {
      threat: { key: "t", color: "#c4570a", opacity: 0.9, lineWidth: 8 },
      // what a move does (code facts): attack, defence, a piece left loose
      attack: { key: "at", color: "#c2362b", opacity: 0.8, lineWidth: 9 },
      defend: { key: "df", color: "#2f7d4f", opacity: 0.8, lineWidth: 9 },
      loose: { key: "lo", color: "#d06a1f", opacity: 0.9, lineWidth: 9 },
      played: { key: "pl", color: "#2f5f6e", opacity: 0.5, lineWidth: 7 },
    },
  },
  highlight: { lastMove: true, check: true },
});

let orientation = "white";
function setOrientation(o) {
  orientation = o;
  board.set({ orientation: o });
  $("eval").classList.toggle("flipped", o === "black");
  renderPlayers();
}

function setEval(pctWhite) {
  $("eval-fill").style.height = `${pctWhite}%`;
}

// ---------------------------------------------------------------- tabs

let tab = "review";
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll("input[name=color]").forEach((r) =>
  r.addEventListener("change", () => { if (!P.state) renderPlayBoard(); }));

function showTab(name) {
  tab = name;
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $("panel-review").hidden = name !== "review";
  $("panel-play").hidden = name !== "play";
  $("panel-practice").hidden = name !== "practice";
  $("nav").hidden = name !== "review";
  $("eval").classList.toggle("off", name !== "review");
  if (name === "review") renderReviewBoard();
  else if (name === "practice") openPractice();
  else {
    renderPlayBoard();
    if (!P.state) showOpponent();       // Maia may have been set up since
  }
}

// ---------------------------------------------------------------- review

const R = { game: null, reviewed: false, cursor: -1, compare: false, polling: null,
            answers: {}, asking: false, node: null, view: "moves", games: [] };

function loadSummary(summary) {
  R.game = summary;
  R.reviewed = false;
  R.cursor = -1;
  R.compare = false;
  R.answers = {};
  R.node = null;
  if (summary.games) R.games = summary.games;
  renderGamePick(summary.index || 0);
  const tree = summary.tree;
  $("viewtabs").hidden = !(tree && tree.annotated);
  setView(tree && tree.annotated ? "notes" : "moves");
  $("ask").hidden = false;
  const h = summary.headers;
  $("game-title").textContent = `${h.White || "?"} – ${h.Black || "?"}`;
  const bits = [h.Result, h.Event && h.Event !== "?" ? h.Event : "", h.Date && !h.Date.includes("?") ? h.Date : ""];
  const full = Math.ceil(summary.moves.length / 2);
  $("game-sub").textContent = bits.filter(Boolean).join(", ") + `. ${full} moves.`;
  $("review-controls").hidden = false;
  $("save-report").hidden = true;
  $("moments").hidden = true;
  $("review-side").value = summary.side || guessSide(h);
  setOrientation(($("review-side").value === "black") ? "black" : "white");
  renderMoveList();
  renderNotes();
  renderGraph();
  renderReviewBoard();
  renderCoach();
  renderAsk();
}

// ---- a file with many games: pick one

function renderGamePick(index) {
  const sel = $("game-pick");
  sel.hidden = R.games.length < 2;
  if (sel.hidden) return;
  sel.innerHTML = R.games.map((g) => `<option value="${g.index}">${g.index + 1}. ${esc(g.white)} – ${esc(g.black)} ${esc(g.result)}${g.annotated ? " ✎" : ""}</option>`).join("");
  sel.value = String(index);
}
$("game-pick").addEventListener("change", async () => {
  const res = await call("select_game", Number($("game-pick").value));
  if (res) loadSummary(res);
});

let settingsCache = null;
function guessSide(h) {
  const me = (settingsCache && settingsCache.player_name || "").toLowerCase();
  if (me && (h.White || "").toLowerCase() === me) return "white";
  if (me && (h.Black || "").toLowerCase() === me) return "black";
  return "both";
}

function renderPlayers() {
  const h = R.game ? R.game.headers : null;
  let top = "", bottom = "";
  if (tab === "review" && h) {
    const w = `<b>${esc(h.White || "White")}</b>`, b = `<b>${esc(h.Black || "Black")}</b>`;
    [top, bottom] = orientation === "white" ? [b, w] : [w, b];
  } else if (tab === "play" && P.state) {
    const me = `<b>${esc((settingsCache && settingsCache.player_name) || "You")}</b>`;
    const opp = `<b>${esc(P.state.opponent)}</b>`;
    [top, bottom] = [opp, me];
  }
  $("player-top").innerHTML = top;
  $("player-bottom").innerHTML = bottom;
}

function renderReviewBoard() {
  if (tab !== "review") return;
  renderPlayers();
  board.set({ movable: { color: undefined, dests: new Map() } });
  if (!R.game) {
    board.set({ fen: START, lastMove: undefined, check: false });
    board.setAutoShapes([]);
    setEval(50);
    return;
  }
  if (R.node !== null) {
    // a move in one of the annotator's variations
    const n = treeNode(R.node);
    board.set({ fen: n.fen, lastMove: n.uci ? sqs(n.uci) : undefined,
                turnColor: n.fen.split(" ")[1] === "w" ? "white" : "black", check: isCheck(n.san) });
    board.setAutoShapes(noteShapes(n));
    setEval(50);
    return;
  }
  const mv = R.cursor >= 0 ? R.game.moves[R.cursor] : null;
  const threat = R.reviewed && mv && mv.threat && mv.threat.uci ? sqs(mv.threat.uci) : null;
  if (mv && R.compare && mv.best_uci) {
    // the position before the move: what was played (red) vs the engine
    // (green), and what the opponent was threatening (dashed)
    board.set({ fen: mv.fen_before, lastMove: undefined, check: false });
    const [pf, pt] = sqs(mv.uci), [bf, bt] = sqs(mv.best_uci);
    const shapes = [{ orig: pf, dest: pt, brush: "red" }, { orig: bf, dest: bt, brush: "green" }];
    if (mv.plan && mv.plan.idea_uci) {           // the idea the engine's move prepares
      const [i1, i2] = sqs(mv.plan.idea_uci);
      shapes.push({ orig: i1, dest: i2, brush: "blue" });
    }
    if (threat) shapes.push({ orig: threat[0], dest: threat[1], brush: "threat" });
    board.setAutoShapes(shapes.concat(factShapes()));
  } else {
    board.set({
      fen: mv ? mv.fen_after : R.game.start_fen,
      lastMove: mv ? sqs(mv.uci) : undefined,
      turnColor: mv ? (mv.color === "white" ? "black" : "white") : "white",
      check: mv ? isCheck(mv.san) : false,
    });
    // A threat the move ignored still stands after it: show it there too.
    const shapes = threat && mv.threat.ignored
      ? [{ orig: threat[0], dest: threat[1], brush: "threat" }] : [];
    board.setAutoShapes(shapes.concat(moveShapes(mv, shapes), factShapes(),
                                      noteShapes(mainNode(R.cursor))));
  }
  const ev = evalAt(R.cursor);
  setEval(ev === null ? 50 : ev);
  markGraph();
}

function moveCell(mv, i, current, onPick, reviewed) {
  const b = document.createElement("button");
  b.className = "mv";
  b.setAttribute("role", "option");
  if (i === current) { b.classList.add("current"); b.setAttribute("aria-selected", "true"); }
  if (reviewed && mv.book) b.classList.add("book");
  if (reviewed && mv.reason) b.classList.add("moment");
  b.innerHTML = esc(figurine(mv.san));
  const m = reviewed ? markOf(mv) : "";
  if (m) b.innerHTML += `<span class="mark j-${mv.label}">${m}</span>`;
  b.title = reviewed && mv.label ? `${JUDGMENT_TEXT[mv.label] || mv.label}${mv.reason ? " — " + mv.reason : ""}` : "";
  if (onPick) b.addEventListener("click", () => onPick(i));
  return b;
}

function fillMoveGrid(el, moves, current, onPick, reviewed) {
  el.innerHTML = "";
  if (!moves.length) return;
  let i = 0;
  if (moves[0].color === "black") {            // game starting with Black to move
    el.append(numCell(moves[0].move_label), document.createElement("span"));
  }
  for (; i < moves.length; i++) {
    const mv = moves[i];
    if (mv.color === "white") el.append(numCell(mv.move_label));
    el.append(moveCell(mv, i, current, onPick, reviewed));
  }
  const cur = el.querySelector(".current");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}
function numCell(label) {
  const s = document.createElement("span");
  s.className = "num";
  s.textContent = label.split(".")[0] + ".";
  return s;
}

function renderMoveList() {
  const el = $("movelist");
  if (!R.game) { el.innerHTML = ""; return; }
  fillMoveGrid(el, R.game.moves, R.cursor, goTo, R.reviewed);
}

function goTo(i) {
  if (!R.game) return;
  R.cursor = Math.max(-1, Math.min(R.game.moves.length - 1, i));
  R.node = null;
  R.compare = false;
  R.facts = null;                    // set again by the move's facts panel
  renderMoveList();
  markNotes();
  renderReviewBoard();
  renderCoach();
  renderMoments();
  renderAsk();
}

// A move inside one of the annotator's variations.
function goToNode(id) {
  const n = treeNode(id);
  if (!n) return;
  if (n.main) return goTo(n.ply);
  R.node = id;
  R.compare = false;
  R.facts = null;
  markNotes();
  renderReviewBoard();
  renderCoach();
  renderAsk();
}

function renderMoments() {
  const el = $("moments");
  if (!R.reviewed) { el.hidden = true; return; }
  const ms = R.game.moves.filter((m) => m.reason);
  el.hidden = false;
  el.innerHTML = ms.length ? "<span>Critical moments</span>" : "<span>No critical moments for this side.</span>";
  for (const m of ms) {
    const b = document.createElement("button");
    b.innerHTML = `${esc(figurine(m.move_label))}<span class="mark j-${m.label}">${markOf(m)}</span>`;
    b.title = m.reason;
    if (m.ply === R.cursor) b.classList.add("on");
    b.addEventListener("click", () => goTo(m.ply));
    el.append(b);
  }
  const errors = ms.filter((m) => BAD.has(m.label)).length;
  if (errors) {
    const b = document.createElement("button");
    b.className = "practice-link";
    b.textContent = `Practise ${errors === 1 ? "this move" : `these ${errors} moves`}`;
    b.title = "Replay the positions and find a better move";
    b.addEventListener("click", () => {
      document.querySelector("input[name=pscope][value=game]").checked = true;
      X.loaded = null;
      showTab("practice");
    });
    el.append(b);
  }
}

// The model's text arrives as "**Heading**: text" lines. The judgment is
// shown separately, so the Assessment line is dropped.
function renderAnnotation(text) {
  const out = [];
  for (const raw of text.split(/\n+/)) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^\*\*(.+?)\*\*:?\s*(.*)$/);
    if (m && /assessment/i.test(m[1])) continue;
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    if (m && /what happened/i.test(m[1])) out.push(`<p>${inline(m[2])}</p>`);
    else if (m) out.push(`<p><b>${esc(m[1])}.</b> ${inline(m[2])}</p>`);
    else out.push(`<p>${inline(line)}</p>`);
  }
  return out.join("");
}

function renderCoach() {
  const el = $("coach");
  if (!R.game) {
    el.innerHTML = `<p class="empty">The coach comments on the moments that decided the game: where you left the opening, your mistakes and what was better, and the moves you got right.</p>`;
    return;
  }
  if (R.node !== null) {
    const n = treeNode(R.node);
    el.innerHTML = `<div class="move">${esc(figurine(n.move_label))}<span class="judgment">${n.nags.map((g) => esc(g.glyph)).join(" ")}</span></div>`
      + `<p class="empty">A move in one of the annotator's variations, not played in the game. The arrow keys follow the variation; the game's moves bring you back.</p>`;
    R.facts = null;
    return;
  }
  if (R.cursor < 0) {
    el.innerHTML = R.reviewed
      ? renderSummary() + `<p class="empty">Pick a critical moment above, or step through with the arrow keys.</p>`
      : `<p class="empty">Choose whose moves to comment on, then review the game. Stockfish analyses every position first; the coach then writes about the critical moments.</p>`;
    bindSummary(el);
    R.facts = null;
    return;
  }
  const mv = R.game.moves[R.cursor];
  const mark = R.reviewed ? markOf(mv) : "";
  let html = `<div class="move">${esc(figurine(mv.move_label))}${mark ? `<span class="j-${mv.label}">${mark}</span>` : ""}`;
  if (R.reviewed) html += `<span class="judgment j-${mv.label}">${esc(JUDGMENT_TEXT[mv.label] || mv.label)}</span>`;
  html += `</div>`;
  if (!R.reviewed) {
    el.innerHTML = html + openingHtml(mv) + notesHtml(mv)
      + `<p class="empty">Review the game to see the coach's comments.</p>`;
    return;
  }
  html += `<div class="verdict">After this move: ${esc(mv.verdict)}.</div>`;
  html += openingHtml(mv);
  const text = Object.values(mv.annotations || {})[0];
  if (BAD.has(mv.label) && mv.best_uci) {
    html += `<button class="compare${R.compare ? " on" : ""}" id="compare">${R.compare ? "Back to the game" : "Show the better move"}</button>`;
    if (R.compare) html += `<span class="legend"><i style="background:#882020"></i>played <i style="background:#15781B;margin-left:10px"></i>${esc(figurine(mv.best_san))}`
      + (mv.plan && mv.plan.idea ? ` <i style="background:#003088;margin-left:10px"></i>its idea: ${esc(figurine(mv.plan.idea))}` : "") + `</span>`;
  }
  if (text) {
    if (mv.reason && mv.reason !== "every-move mode") html += `<div class="why">Commented because: ${esc(mv.reason)}.</div>`;
    html += renderAnnotation(text) + legendHtml(mv) + planHtml(mv);
    const removed = (Object.values(mv.stats || {})[0] || {}).removed || 0;
    if (removed) html += `<div class="checked" title="The coach's statements are checked against the board. A statement that was not true was taken out rather than shown.">${removed === 1 ? "One sentence was" : removed + " sentences were"} left out because ${removed === 1 ? "it was" : "they were"} not true on the board.</div>`;
    html += factsHtml(mv);
  } else if (mv.book) {
    html += notesHtml(mv) + (mv.opening && mv.opening.name ? "" : `<p class="empty">A standard opening move.</p>`);
  } else {
    html += notesHtml(mv) + `<p class="empty">No comment needed here.${mv.best_san && mv.best_san !== mv.san ? ` The engine slightly preferred ${esc(figurine(mv.best_san))}.` : ""}</p>`;
  }
  el.innerHTML = html;
  bindSummary(el);
  bindFacts(el, mv);
  renderReviewBoard();               // circles for the facts, if the panel is open
  const c = $("compare");
  if (c) c.addEventListener("click", () => { R.compare = !R.compare; renderReviewBoard(); renderCoach(); });
}

// ---- the annotator's notes: comments, variations, symbols, arrows

const treeNode = (id) => (R.game && R.game.tree ? R.game.tree.nodes[id] : null);
const mainNode = (ply) => (R.game && R.game.tree ? treeNode(ply < 0 ? 0 : R.game.tree.mainline[ply]) : null);
const BRUSH = { green: "green", red: "red", blue: "blue", yellow: "yellow" };

function noteShapes(n) {
  if (!n) return [];
  return n.arrows.map((a) => ({ orig: a.from, dest: a.to, brush: BRUSH[a.color] || "green" }))
    .concat(n.circles.map((c) => ({ orig: c.square, brush: BRUSH[c.color] || "green" })));
}

function commentText(c) {
  if (!c) return "";
  const lang = (settingsCache && settingsCache.comment_language) || "en";
  const en = c.en || "", de = c.de || "";
  if (lang === "both") return [en, de].filter(Boolean).map((t, i) => i ? `<span class="de">${esc(figurineText(t))}</span>` : esc(figurineText(t))).join(" ");
  const t = lang === "de" ? (de || en) : (en || de);
  return esc(figurineText(t));
}

function moveTok(n, withNumber) {
  const num = n.white ? `${n.move_label.split(".")[0]}. ` : withNumber ? `${n.move_label.split("...")[0]}... ` : "";
  const moveGlyph = n.nags.filter((g) => g.move).map((g) => g.glyph).join("");
  const posGlyph = n.nags.filter((g) => !g.move).map((g) => `<span class="pos" title="${esc(g.words)}">${esc(g.glyph)}</span>`).join(" ");
  const cls = "tok" + (n.main ? " main" : "") + (moveGlyph.includes("?") ? " bad" : moveGlyph.includes("!") ? " good" : "");
  const title = n.nags.map((g) => g.words).filter(Boolean).join(", ");
  let html = "";
  const before = commentText(n.before);
  if (before) html += `<span class="cmt">${before}</span> `;
  html += `<a href="#" class="${cls}" data-node="${n.id}"${title ? ` title="${esc(title)}"` : ""}>${esc(num)}${esc(figurine(n.san))}${moveGlyph ? `<b>${esc(moveGlyph)}</b>` : ""}</a>`;
  if (posGlyph) html += ` ${posGlyph}`;
  const c = commentText(n.comment);
  if (c) html += n.main ? `</p><p class="cmt main">${c}</p><p class="line">` : ` <span class="cmt">${c}</span>`;
  return { html, broke: Boolean(c) };
}

// Main line in paragraphs, variations inline in brackets, as in a book.
function lineFrom(id, inVariation) {
  let out = "", cur = id, number = true;
  while (treeNode(cur).children.length) {
    const kids = treeNode(cur).children;
    const t = moveTok(treeNode(kids[0]), number);
    out += t.html + " ";
    number = t.broke;
    for (const alt of kids.slice(1)) {
      const a = moveTok(treeNode(alt), true);
      const rest = lineFrom(alt, true);
      out += inVariation ? `<span class="var">(${a.html} ${rest})</span> `
                         : `</p><p class="var-block">${a.html} ${rest}</p><p class="line">`;
      number = true;
    }
    cur = kids[0];
  }
  return out;
}

function renderNotes() {
  const el = $("annotations");
  const tree = R.game && R.game.tree;
  if (!tree || !tree.annotated) { el.innerHTML = ""; return; }
  const intro = commentText(tree.nodes[0].comment);
  const h = R.game.headers;
  let html = h.Annotator ? `<p class="by">Annotated by ${esc(h.Annotator)}</p>` : "";
  if (intro) html += `<p class="cmt main">${intro}</p>`;
  html += `<p class="line">${lineFrom(0, false)}</p>`;
  el.innerHTML = html.replace(/<p class="line">\s*<\/p>/g, "");
  el.querySelectorAll("a.tok").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    goToNode(Number(a.dataset.node));
  }));
  markNotes();
}

function markNotes() {
  const el = $("annotations");
  if (!el || el.hidden || !R.game || !R.game.tree) return;
  const cur = R.node !== null ? R.node : (R.cursor >= 0 ? R.game.tree.mainline[R.cursor] : -1);
  el.querySelectorAll("a.tok.cur").forEach((a) => a.classList.remove("cur"));
  const a = el.querySelector(`a.tok[data-node="${cur}"]`);
  if (a) {
    a.classList.add("cur");
    a.scrollIntoView({ block: "nearest" });
  }
}

function setView(v) {
  R.view = v;
  document.querySelectorAll("#viewtabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
  $("movelist").hidden = v !== "moves";
  $("annotations").hidden = v !== "notes";
  markNotes();
}
document.querySelectorAll("#viewtabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

// ---- evaluation graph: our review's evaluation once there is one, else the
// annotator's engine (ChessBase %evp). White's winning chance per move;
// click to go to that move.

function evalSeries() {
  if (!R.game) return null;
  if (R.reviewed) return { src: "review", vals: R.game.moves.map((m) => winPctWhite(m.score_cp, m.score_mate)) };
  const evp = R.game.tree && R.game.tree.evp;
  if (evp && evp.length > 1) return { src: "annotator", vals: R.game.moves.map((_, i) => (evp[i + 1] === undefined || evp[i + 1] === null ? null : winPctWhite(evp[i + 1], null))) };
  return null;
}

function evalAt(ply) {
  const s = evalSeries();
  if (!s || ply < 0) return R.reviewed || s ? 50 : null;
  const v = s.vals[ply];
  return v === null || v === undefined ? null : v;
}

function renderGraph() {
  const el = $("evalgraph");
  const s = evalSeries();
  if (!s) { el.hidden = true; el.innerHTML = ""; return; }
  const n = s.vals.length, W = 1000, H = 100;
  const x = (i) => (n <= 1 ? 0 : (i / (n - 1)) * W), y = (v) => H - (v / 100) * H;
  let path = "", started = false, lastX = 0;
  s.vals.forEach((v, i) => {
    if (v === null) return;
    path += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    started = true; lastX = x(i);
  });
  const firstX = x(s.vals.findIndex((v) => v !== null));
  // White's share fills from the bottom up to the line, like the eval bar.
  const area = started ? `${path}L${lastX},${H}L${firstX},${H}Z` : "";
  el.hidden = false;
  el.innerHTML = `<div class="g-head"><span>${s.src === "review" ? "Evaluation (Stockfish, this review)" : "Evaluation (the annotator's engine)"}</span><span class="g-tip" id="g-tip"></span></div>`
    + `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="White's winning chance move by move">`
    + `<line class="mid" x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}"/>`
    + `<path class="area" d="${area}"/><path class="ln" d="${path}"/>`
    + `<line class="cur" id="g-cur" x1="-10" y1="0" x2="-10" y2="${H}"/>`
    + `<line class="hov" id="g-hov" x1="-10" y1="0" x2="-10" y2="${H}"/></svg>`;
  const svg = el.querySelector("svg");
  const plyAt = (ev) => {
    const r = svg.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.round(((ev.clientX - r.left) / r.width) * (n - 1))));
  };
  svg.addEventListener("mousemove", (ev) => {
    const i = plyAt(ev), v = s.vals[i];
    $("g-hov").setAttribute("x1", x(i)); $("g-hov").setAttribute("x2", x(i));
    $("g-tip").textContent = `${figurine(R.game.moves[i].move_label)}: ${v === null ? "no evaluation" : `White ${Math.round(v)}%`}`;
  });
  svg.addEventListener("mouseleave", () => { $("g-hov").setAttribute("x1", -10); $("g-hov").setAttribute("x2", -10); $("g-tip").textContent = ""; });
  svg.addEventListener("click", (ev) => goTo(plyAt(ev)));
  markGraph();
}

function markGraph() {
  const c = $("g-cur");
  const s = evalSeries();
  if (!c || !s) return;
  const n = s.vals.length;
  const i = R.node !== null ? -1 : R.cursor;
  const xx = i < 0 || n <= 1 ? -10 : (i / (n - 1)) * 1000;
  c.setAttribute("x1", xx); c.setAttribute("x2", xx);
}

// ---- arrows for what a move does: the move itself (from → to, faint), then
// what it attacks (red), defends (green), the threat it left (dashed) and
// a piece it left without a defender (orange ring). Facts from code.

const SHOW_ARROWS = { on: true };
function moveShapes(mv, already = []) {
  if (!mv || !SHOW_ARROWS.on) return [];
  const [f, t] = sqs(mv.uci);
  const out = [{ orig: f, dest: t, brush: "played" }];
  const seen = new Set(already.map((s) => s.orig + s.dest));
  for (const s of mv.shapes || []) {
    if (s.square) out.push({ orig: s.square, brush: s.kind === "loose" ? "loose" : "yellow" });
    else if (!seen.has(s.from + s.to)) out.push({ orig: s.from, dest: s.to, brush: s.kind === "threat" ? "threat" : s.kind });
  }
  return out;
}

function legendHtml(mv) {
  const kinds = new Set((mv.shapes || []).map((s) => s.kind));
  if (mv.threat && mv.threat.ignored) kinds.add("threat");
  if (!kinds.size || !SHOW_ARROWS.on) return "";
  const item = { attack: `<i class="lg attack"></i>attacks`, defend: `<i class="lg defend"></i>defends`,
                 threat: `<i class="lg threat"></i>threat left standing`, loose: `<i class="lg loose"></i>left without a defender` };
  return `<div class="legend-arrows">${["attack", "defend", "threat", "loose"].filter((k) => kinds.has(k)).map((k) => `<span>${item[k]}</span>`).join("")}</div>`;
}

// ---- the plan behind a quiet engine move (after DecodeChess's Plans):
// idea, why it fails at once, the quiet move first, the line after it.

function planHtml(mv) {
  const p = mv.plan;
  if (!p || !p.move) return "";
  const line = (l) => (l || []).map((m) => esc(figurine(m))).join(" ");
  const whose = p.played ? "your move" : "the engine's move";
  return `<section class="plan"><h4>The plan behind ${esc(figurine(p.move))} <span>(${whose})</span></h4>`
    + `<p><b class="k idea">Idea</b> ${esc(figurine(p.idea))}: ${esc(p.idea_words)}. <span class="ln">${line(p.idea_line)}</span></p>`
    + `<p><b class="k problem">Problem</b> Played at once, ${esc(figurine(p.idea))} gives a winning chance of ${Math.round(p.now_win)}% instead of ${Math.round(p.plan_win)}%.`
    + (p.problem_text ? ` ${esc(figurineText(p.problem_text))}.` : "") + `</p>`
    + `<p><b class="k solution">Solution</b> ${esc(figurine(p.move))} first.</p>`
    + `<p><b class="k outcome">Outcome</b> <span class="ln">${line(p.outcome)}</span></p></section>`;
}

// ---- what code can say about any move: the opening it reaches, where it
// leaves theory, and what it does (forks, threats, castling made possible).

function openingHtml(mv) {
  let html = "";
  if (mv.opening && mv.opening.name) {
    html += `<div class="opening"><span class="eco">${esc(mv.opening.eco)}</span> ${esc(mv.opening.name)}</div>`;
    if (mv.opening.idea) html += `<p class="idea">${esc(mv.opening.idea)}</p>`;
  }
  if (mv.left_theory && (mv.left_theory.opening || mv.left_theory.source)) {
    const n = mv.move_label.match(/^(\d+)(\.\.\.|\. )/);
    const num = n ? (n[2] === ". " ? `${n[1]}. ` : `${n[1]}...`) : "";
    const alts = (mv.left_theory.alternatives || []).map((a) =>
      `<b>${esc(figurine(num + a.san))}</b>${a.name ? ` (${esc(a.name)})` : ""}`);
    // Without an opening book the only evidence is the openings table, so
    // say exactly that rather than claiming the game left "theory".
    const named = mv.left_theory.source === "names";
    const what = named ? "This move leaves the named opening lines" : "This move leaves known opening theory";
    const cont = named ? "the named lines continue with" : "theory continues with";
    html += `<p class="theory">${what}${alts.length ? `; ${cont} ${alts.join(" or ")}` : ""}.</p>`;
  }
  return html;
}

function notesHtml(mv) {
  const notes = mv.notes || [];
  if (!notes.length) return legendHtml(mv);
  return `<ul class="notes">${notes.map((n) => `<li>${esc(figurineText(n))}.</li>`).join("")}</ul>` + legendHtml(mv);
}

// ---- facts behind a comment: the verified facts the coach was given, with
// their squares circled on the board while the panel is open (or while the
// pointer is on one fact).

function factSquares(text) {
  const out = [];
  for (const m of text.matchAll(/\b(?:on|from|to|at) ([a-h][1-8])\b/g)) out.push(m[1]);
  return [...new Set(out)];
}

function factShapes() {
  if (!R.facts) return [];
  const squares = R.facts.hover != null ? R.facts.hover : R.facts.all;
  return squares.map((s) => ({ orig: s, brush: "paleBlue" }));
}

function factsHtml(mv) {
  const facts = mv.key_facts || [];
  let threat = "";
  if (mv.threat && mv.threat.text) {
    threat = `<p class="threat"><i></i>${esc(mv.threat.text)}${mv.threat.ignored
      ? " — the move did not deal with it." : " — the move dealt with it."}</p>`;
  }
  if (!facts.length && !threat) return "";
  const items = facts.map((f) => `<li data-squares="${factSquares(f).join(" ")}">${esc(figurineText(f))}</li>`).join("");
  return `<details class="facts" id="facts"${R.factsOpen ? " open" : ""}><summary>Facts behind this comment</summary>`
    + threat + (items ? `<ul>${items}</ul>` : "")
    + `<p class="note">Verified by code before the coach wrote anything. Circled squares are the ones these facts name.</p></details>`;
}

function bindFacts(el, mv) {
  const d = el.querySelector("#facts");
  if (!d) { R.facts = null; return; }
  const all = [...new Set((mv.key_facts || []).flatMap(factSquares))];
  R.facts = d.open ? { all, hover: null } : null;
  d.addEventListener("toggle", () => {
    R.factsOpen = d.open;
    R.facts = d.open ? { all, hover: null } : null;
    renderReviewBoard();
  });
  d.querySelectorAll("li").forEach((li) => {
    const own = (li.dataset.squares || "").split(" ").filter(Boolean);
    li.addEventListener("mouseenter", () => { if (R.facts) { R.facts.hover = own; renderReviewBoard(); } });
    li.addEventListener("mouseleave", () => { if (R.facts) { R.facts.hover = null; renderReviewBoard(); } });
  });
}

// ---- game summary: written by code from stored facts; each claim can show
// the facts it rests on, and its moves are links into the game.

function claimHtml(c, linkMoves) {
  let text = esc(figurineText(c.text));
  if (linkMoves) {
    for (const p of c.plies) {
      const mv = R.game.moves[p];
      if (!mv) continue;
      const label = esc(figurineText(mv.move_label));
      text = text.split(label).join(`<a href="#" class="ply" data-ply="${p}">${label}</a>`);
    }
  }
  const facts = (c.facts || []).map((f) => `<li>${esc(figurineText(f))}</li>`).join("");
  return `<li>${text}${facts ? `<details><summary>Facts behind this</summary><ul>${facts}</ul></details>` : ""}</li>`;
}

function renderSummary() {
  const sum = R.game.summary || [];
  const pats = R.game.patterns || [];
  if (!sum.length && !pats.length) return "";
  let html = `<section class="summary" id="summary"><h3>Game summary</h3><ul>`;
  html += sum.map((c) => claimHtml(c, true)).join("") + `</ul>`;
  if (pats.length) {
    html += `<h3>Across your reviewed games</h3><ul>` + pats.map((c) => claimHtml(c, false)).join("") + `</ul>`;
  }
  return html + `</section>`;
}

function bindSummary(el) {
  el.querySelectorAll("a.ply").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    goTo(Number(a.dataset.ply));
  }));
}

// ---- ask the coach: answers belong to a position, so they are kept per move

function renderAsk() {
  const el = $("ask-answer");
  const list = R.answers[R.cursor] || [];
  let html = "";
  for (const a of list) {
    if (a.question) html += `<p class="q">You asked: ${esc(a.question)}</p>`;
    else html += `<p class="q">Explain this position</p>`;
    if (a.pending) { html += `<p class="thinking">The coach is checking the position with the engine…</p>`; continue; }
    html += renderAnnotation(a.text);
    const meta = [];
    if (a.removed && a.removed.length)
      meta.push(`${a.removed.length === 1 ? "One sentence was" : a.removed.length + " sentences were"} left out because ${a.removed.length === 1 ? "it was" : "they were"} not true on the board.`);
    if (!(a.removed && a.removed.length)) meta.push("Checked against the board.");
    if (a.secs) meta.push(`${a.secs}s`);
    if (meta.length) html += `<p class="meta">${esc(meta.join(" · "))}</p>`;
  }
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
  $("explain-btn").disabled = R.asking;
  $("ask-btn").disabled = R.asking;
}

async function askCoach(question) {
  if (!R.game || R.asking) return;
  const ply = R.cursor;
  const entry = { question, pending: true };
  (R.answers[ply] = R.answers[ply] || []).push(entry);
  R.asking = true;
  renderAsk();
  const res = question ? await call("ask_coach", ply, question)
                       : await call("explain_position", ply);
  R.asking = false;
  if (res) Object.assign(entry, res, { pending: false });
  else R.answers[ply] = R.answers[ply].filter((a) => a !== entry);
  if (R.cursor === ply) renderAsk(); else { $("explain-btn").disabled = false; $("ask-btn").disabled = false; }
}

$("explain-btn").addEventListener("click", () => askCoach(""));
$("ask-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("ask-q").value.trim();
  if (!q) return;
  $("ask-q").value = "";
  askCoach(q);
});

// ---- running a review

$("open-pgn").addEventListener("click", async () => {
  const res = await call("open_pgn_dialog");
  if (res && !res.cancelled) loadSummary(res);
});

$("start-review").addEventListener("click", startReview);
async function startReview() {
  const res = await call("start_review", $("review-side").value, false);
  if (!res) return;
  $("start-review").disabled = true;
  $("progress").hidden = false;
  $("progress-fill").style.width = "0%";
  $("progress-text").textContent = "Starting";
  clearInterval(R.polling);
  R.polling = setInterval(pollReview, 500);
}

async function pollReview() {
  const s = await api().review_status();
  if (s.state === "running") {
    // engine analysis is the first ~40% of the bar, comments the rest
    const frac = s.total ? s.done / s.total : 0;
    const pct = s.stage === "engine" ? frac * 40 : 40 + frac * 60;
    $("progress-fill").style.width = `${pct}%`;
    $("progress-text").textContent = s.stage === "engine" && s.message.startsWith("Analysing")
      ? `Analysing positions ${s.done} of ${s.total}` : s.message;
    return;
  }
  clearInterval(R.polling);
  $("start-review").disabled = false;
  $("progress").hidden = true;
  if (s.state === "finished") {
    const res = s.result;
    R.game.moves = res.moves;
    R.game.summary = res.summary || [];
    R.game.patterns = res.patterns || [];
    R.reviewed = true;
    X.loaded = null;                // the practice list follows the latest review
    renderGraph();
    $("save-report").hidden = false;
    toast(`Review complete: ${res.moves.filter((m) => m.reason).length} critical moments.`);
    goTo(-1);                       // open on the game summary
  } else if (s.state === "error") {
    toast(s.message, 6000);
  } else if (s.state === "cancelled") {
    toast("Review stopped.");
  }
}

$("cancel-review").addEventListener("click", () => api().cancel_review());
$("save-report").addEventListener("click", async () => {
  const res = await call("save_report_dialog");
  if (res && res.saved) toast(`Saved ${res.saved}`);
});

document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => nav(b.dataset.nav)));
function nav(what) {
  if (what === "flip") return setOrientation(orientation === "white" ? "black" : "white");
  if (what === "arrows") {
    SHOW_ARROWS.on = !SHOW_ARROWS.on;
    $("arrows-btn").setAttribute("aria-pressed", String(SHOW_ARROWS.on));
    renderReviewBoard();
    renderCoach();
    return;
  }
  if (!R.game) return;
  if (R.node !== null) {                       // inside a variation: follow it
    const n = treeNode(R.node);
    if (what === "next" && n.children.length) return goToNode(n.children[0]);
    if (what === "prev") return goToNode(n.parent);
    if (what === "first" || what === "last") return goTo(what === "first" ? -1 : R.game.moves.length - 1);
    return;
  }
  const last = R.game.moves.length - 1;
  goTo({ first: -1, prev: R.cursor - 1, next: R.cursor + 1, last }[what]);
}
document.addEventListener("keydown", (e) => {
  if (tab !== "review" || e.target.closest("input, select, dialog")) return;
  const k = { ArrowLeft: "prev", ArrowRight: "next", Home: "first", End: "last", f: "flip", a: "arrows" }[e.key];
  if (k) { e.preventDefault(); nav(k); }
});

// ---------------------------------------------------------------- play

const P = { state: null, busy: false };

$("elo").addEventListener("input", () => {
  $("elo-out").textContent = $("elo").value;
  showOpponent();
});

// Say who the player will actually face: Maia at the chosen rating, or, when
// Maia is missing, Stockfish at its effective (possibly clamped) strength.
let opponentSeq = 0;
async function showOpponent() {
  const seq = ++opponentSeq;
  const op = await call("play_opponent", Number($("elo").value));
  if (!op || seq !== opponentSeq) return;
  opponentNote($("opponent-note"), op);
  $("play-sub").textContent = op.human_like
    ? "Maia plays like a human of the rating you choose, mistakes included. When the game ends, review it here."
    : "Pick a rating and play. When the game ends, review it here.";
}

function opponentNote(el, op) {
  el.textContent = op ? op.note : "";
  el.classList.toggle("warn", !!op && !op.human_like);
}

$("start-game").addEventListener("click", startGame);
$("new-game").addEventListener("click", () => {
  $("play-live").hidden = true;
  $("play-setup").hidden = false;
  showOpponent();
  P.state = null;
  renderPlayBoard();
});

async function startGame() {
  const color = document.querySelector("input[name=color]:checked").value;
  $("start-game").disabled = true;
  $("play-status").textContent = "Setting up the board…";
  const st = await call("new_game", color, Number($("elo").value));
  $("start-game").disabled = false;
  if (!st) return;
  $("play-setup").hidden = true;
  $("play-live").hidden = false;
  applyPlayState(st);
}

function applyPlayState(st) {
  P.state = st;
  P.busy = false;
  setOrientation(st.orientation);
  renderPlayBoard();
  $("play-status").textContent = st.status;
  opponentNote($("play-opponent"), st.opponent_info);
  fillMoveGrid($("play-moves"), st.moves, st.moves.length - 1, null, false);
  $("takeback").hidden = st.over;
  $("resign").hidden = st.over;
  $("review-played").hidden = !st.over;
  $("save-pgn").hidden = !st.over && st.moves.length === 0;
  $("new-game").hidden = !st.over;
}

function renderPlayBoard() {
  if (tab !== "play") return;
  renderPlayers();
  setEval(50);
  board.setAutoShapes([]);
  if (!P.state) {
    const c = document.querySelector("input[name=color]:checked").value;
    setOrientation(c === "black" ? "black" : "white");
    board.set({ fen: START, lastMove: undefined, check: false, movable: { color: undefined, dests: new Map() } });
    return;
  }
  const st = P.state;
  board.set({
    fen: st.fen,
    turnColor: st.turn,
    lastMove: st.last_move ? sqs(st.last_move) : undefined,
    check: st.check,
    movable: {
      free: false,
      color: P.busy || st.over ? undefined : st.orientation,
      dests: new Map(Object.entries(st.dests)),
      showDests: true,
      events: { after: onPlayerMove },
    },
  });
}

async function onPlayerMove(orig, dest) {
  let uci = orig + dest;
  if (await api().needs_promotion(orig, dest) === true) {
    const piece = await pickPromotion();
    if (!piece) { renderPlayBoard(); return; }
    uci += piece;
  }
  P.busy = true;
  board.set({ movable: { color: undefined } });
  $("play-status").textContent = `${P.state.opponent} is thinking…`;
  const st = await call("play_move", uci);
  if (!st) { P.busy = false; renderPlayBoard(); $("play-status").textContent = P.state.status; return; }
  applyPlayState(st);
}

function pickPromotion() {
  return new Promise((resolve) => {
    const box = $("promo");
    box.hidden = false;
    const done = (p) => { box.hidden = true; box.onclick = null; resolve(p); };
    box.onclick = (e) => {
      const b = e.target.closest("button");
      done(b ? b.dataset.piece : null);
    };
  });
}

$("takeback").addEventListener("click", async () => { const st = await call("takeback"); if (st) applyPlayState(st); });
$("resign").addEventListener("click", async () => { const st = await call("resign"); if (st) applyPlayState(st); });
$("save-pgn").addEventListener("click", async () => {
  const res = await call("save_pgn_dialog");
  if (res && res.saved) toast(`Saved ${res.saved}`);
});
$("review-played").addEventListener("click", async () => {
  const summary = await call("review_played_game");
  if (!summary) return;
  showTab("review");
  loadSummary(summary);
  startReview();
});

// ---------------------------------------------------------------- settings

const SETTINGS_FIELDS = ["stockfish_path", "book_path", "maia_cmd", "model", "player_name", "player_elo", "depth",
                         "comment_language"];
$("open-settings").addEventListener("click", async () => {
  const s = await call("get_settings");
  if (!s) return;
  settingsCache = s;
  const f = $("settings-form");
  for (const k of SETTINGS_FIELDS) f.elements[k].value = s[k] ?? "";
  $("settings-file").textContent = `Saved to ${s.config_file}`;
  $("settings-error").hidden = true;
  $("settings").showModal();
});
$("settings-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;   // let the dialog close
  e.preventDefault();
  const f = e.target;
  const values = Object.fromEntries(SETTINGS_FIELDS.map((k) => [k, f.elements[k].value.trim()]));
  const res = await api().save_settings(values);
  if (res.error) {
    $("settings-error").textContent = res.error;
    $("settings-error").hidden = false;
    return;
  }
  settingsCache = res;
  $("settings").close();
  toast("Settings saved.");
  if (R.game && R.game.tree) renderNotes();
});

// ---------------------------------------------------------------- splitter
// Drag the bar under the moves to give the comments more room (or less);
// remembered in this browser only.

(function splitter() {
  const bar = $("splitter");
  const set = (px) => document.documentElement.style.setProperty("--list-h", `${Math.round(px)}px`);
  try { const v = Number(localStorage.getItem("listH")); if (v > 40) set(v); } catch (e) { /* storage off */ }
  bar.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    const pane = !$("annotations").hidden ? $("annotations") : $("movelist");
    const top = pane.getBoundingClientRect().top;
    bar.classList.add("drag");
    const move = (e) => set(Math.max(60, Math.min(window.innerHeight * 0.7, e.clientY - top)));
    const up = () => {
      bar.classList.remove("drag");
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      try { localStorage.setItem("listH", String(pane.getBoundingClientRect().height)); } catch (e) { /* storage off */ }
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
})();

// ---------------------------------------------------------------- practice
//
// Your reviewed mistakes come back as positions to solve. The board takes
// one move; Stockfish (in Python) judges it against the review's numbers.

const X = { drills: [], i: 0, result: null, loaded: null, busy: false };
const VERDICT = { best: "Correct", good: "That works too", repeat: "Same move as in the game",
                  miss: "Not quite", revealed: "The answer" };

document.querySelectorAll("input[name=pscope]").forEach((r) =>
  r.addEventListener("change", () => { X.loaded = null; openPractice(); }));
$("drill-next").addEventListener("click", () => {
  if (!X.drills.length) return;
  X.i = (X.i + 1) % X.drills.length;
  X.result = null;
  renderDrill();
});
$("drill-retry").addEventListener("click", () => { X.result = null; renderDrill(); });
$("drill-reveal").addEventListener("click", () => submitDrill(null));

async function openPractice() {
  let scope = document.querySelector("input[name=pscope]:checked").value;
  if (scope === "game" && !R.reviewed) {
    scope = "all";
    document.querySelector("input[name=pscope][value=all]").checked = true;
  }
  if (X.loaded !== scope) {
    X.drills = []; X.result = null; X.i = 0;
    renderDrill();
    const res = await call("practice_drills", scope);
    X.loaded = scope;
    X.drills = res ? res.drills : [];
    $("practice-empty").textContent = res ? res.empty : "";
  }
  renderDrill();
}

const curDrill = () => X.drills[X.i] || null;

function lineText(fen, sans) {
  const parts = fen.split(" ");
  let n = Number(parts[5]) || 1, white = parts[1] === "w";
  return sans.map((san, k) => {
    const s = white ? `${n}. ${san}` : (k === 0 ? `${n}...${san}` : san);
    if (!white) n += 1;
    white = !white;
    return figurine(s);
  }).join(" ");
}

function renderDrill() {
  const d = curDrill();
  $("practice-empty").hidden = !!d || !X.loaded;
  $("drill").hidden = !d;
  renderPlayers();
  setEval(50);
  if (!d) {
    if (tab === "practice") {
      board.set({ fen: START, lastMove: undefined, check: false, movable: { color: undefined, dests: new Map() } });
      board.setAutoShapes([]);
    }
    return;
  }
  const where = d.game_name ? ` · ${d.game_name}` : "";
  const p = d.progress || {};
  const record = p.tries ? ` · solved ${p.solved} of ${p.tries} tries` : "";
  $("drill-count").textContent = `Exercise ${X.i + 1} of ${X.drills.length}${where}${record}`;
  const side = d.color === "white" ? "White" : "Black";
  $("drill-prompt").innerHTML = `${side} to move. In the game you played <b>${esc(figurine(d.move_label))}</b>, ${/^[aeiou]/i.test(d.label) ? "an" : "a"} ${esc(d.label.toLowerCase())}. Find a better move.`;
  const r = X.result;
  $("drill-reveal").hidden = !!r;
  $("drill-retry").hidden = !r || r.verdict === "best" || r.verdict === "revealed";
  $("drill-result").innerHTML = r ? drillResultHtml(d, r) : "";
  if (tab !== "practice") return;
  if (orientation !== d.color) setOrientation(d.color);
  const answered = r && r.answer_san;
  board.set({
    fen: d.fen,
    turnColor: d.color,
    lastMove: undefined,
    check: false,
    movable: {
      free: false,
      color: r || X.busy ? undefined : d.color,
      dests: new Map(Object.entries(d.dests || {})),
      showDests: true,
      events: { after: onDrillMove },
    },
  });
  const shapes = [];
  if (r) {
    if (answered && r.verdict !== "best") {
      const u = r.answer_uci || "";
      if (u) shapes.push({ orig: u.slice(0, 2), dest: u.slice(2, 4), brush: r.verdict === "good" ? "played" : "attack" });
    }
    shapes.push({ orig: d.best_uci.slice(0, 2), dest: d.best_uci.slice(2, 4), brush: "defend" });
  }
  board.setAutoShapes(shapes);
}

function drillResultHtml(d, r) {
  const cls = r.verdict === "best" || r.verdict === "good" ? "ok" : "no";
  let html = `<p class="verdict ${cls}"><b>${esc(VERDICT[r.verdict] || "")}.</b> ${esc(figurineText(r.message))}</p>`;
  if (r.line && r.line.length) html += `<p class="line"><b class="k">Engine line</b> ${esc(lineText(d.fen, r.line))}</p>`;
  if (d.explanation) html += `<div class="drill-why"><b class="k">The coach said in the review</b>${renderAnnotation(d.explanation)}</div>`;
  return html;
}

async function onDrillMove(orig, dest) {
  const d = curDrill();
  if (!d) return;
  let uci = orig + dest;
  const piece = board.state.pieces.get(dest);
  if (piece && piece.role === "pawn" && (dest[1] === "8" || dest[1] === "1")) {
    const promo = await pickPromotion();
    if (!promo) { renderDrill(); return; }
    uci += promo;
  }
  await submitDrill(uci);
}

async function submitDrill(uci) {
  const d = curDrill();
  if (!d || X.busy) return;
  X.busy = true;
  board.set({ movable: { color: undefined } });
  $("drill-result").innerHTML = `<p class="sub">Checking with Stockfish…</p>`;
  const res = uci ? await call("practice_answer", d.id, uci) : await call("practice_reveal", d.id);
  X.busy = false;
  if (!res) { renderDrill(); return; }
  res.answer_uci = uci || "";
  d.progress = res.progress || d.progress;
  X.result = res;
  renderDrill();
  if (uci) board.set({ fen: d.fen });        // show the position, arrows carry the moves
}

// ---------------------------------------------------------------- start

async function init() {
  settingsCache = await api().get_settings();
  if (settingsCache && !settingsCache.error && !settingsCache.stockfish_path) {
    toast("Set the Stockfish path in Settings before reviewing a game.", 6000);
  }
  renderCoach();
  showTab("review");
}
if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init, { once: true });
