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
  if (["!!", "!", "!?", "?!", "?", "??"].includes(mv.mark)) return mv.mark;
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
// Tactical motifs (motifs.py), found by code in the engine's lines.
const MOTIFS = { mate: "a forced mate", back_rank_mate: "a back-rank mate", double_check: "a double check",
  fork: "a fork", pin: "a pin", skewer: "a skewer", discovered_attack: "a discovered attack",
  hanging_piece: "a hanging piece", capturing_defender: "taking a defender", sacrifice: "a sacrifice",
  promotion: "a promotion", advanced_pawn: "an advanced pawn" };
function tacticsHtml(mv) {
  const t = mv.tactics || {};
  const say = (xs) => (xs || []).map((x) => MOTIFS[x] || x).join(", ");
  const parts = [];
  if ((t.allowed || []).length) parts.push(`<span><b>Allowed</b> ${esc(say(t.allowed))}</span>`);
  if ((t.missed || []).length) parts.push(`<span><b>The engine had</b> ${esc(say(t.missed))}</span>`);
  return parts.length ? `<p class="tactics" title="Tactical motifs found by code in the engine's lines">${parts.join(" · ")}</p>` : "";
}
// Said under every comment, so no one reads the model's opinions as verified.
const CHECKED_NOTE = `<div class="checked scope" title="Checked: pieces on squares, attacks and defences between named pieces, pins, forks, king shelter, files opened, material, legal moves. Not checked: judgements such as strong, weak, active or the initiative, and wording the checker does not recognise.">Facts about the pieces were checked against the board; judgements are the coach's opinion.</div>`;

function winPctWhite(cp, mate) {
  if (mate !== null && mate !== undefined) return mate > 0 ? 100 : mate < 0 ? 0 : 50;
  if (cp === null || cp === undefined) return 50;
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sqs = (uci) => [uci.slice(0, 2), uci.slice(2, 4)];
const isCheck = (san) => /[+#]$/.test(san || "");
const other = (c) => (c === "white" ? "black" : "white");
// The king in check after a move: the opponent's. Given as a colour, the
// board highlights that king whatever side it thinks is to move.
const checkedSide = (mv) => (mv && isCheck(mv.san) ? other(mv.color) : false);

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
  $("panel-games").hidden = name !== "games";
  $("nav").hidden = name !== "review";
  $("eval").classList.toggle("off", name !== "review");
  if (name === "review") renderReviewBoard();
  else if (name === "practice") openPractice();
  else if (name === "games") { renderGamesBoard(); openGames(); }
  else {
    renderPlayBoard();
    if (!P.state) showOpponent();       // Maia may have been set up since
  }
}

// ---------------------------------------------------------------- review

const R = { game: null, reviewed: false, cursor: -1, compare: false, polling: null,
            answers: {}, asking: false, node: null, view: "moves", games: [],
            running: false, follow: true, live: null };   // live: [cp, mate] per position while reviewing

// A finished review onto the open game: a new one, or one kept from before.
function applyReview(res) {
  R.game.moves = res.moves;
  R.game.summary = res.summary || [];
  R.game.patterns = res.patterns || R.game.patterns || [];
  R.game.models = res.models || [];
  R.game.accuracy = res.accuracy || {};
  R.game.stats = res.stats || {};
  R.reviewed = true;
  X.loaded = null;                // the practice list follows the latest review
  renderGraph();
  // The coach's comments are now part of the game: read them in the
  // Annotations pane, alongside the annotator's when the PGN had them.
  $("viewtabs").hidden = !R.game.tree;
  $("view-table").hidden = false;
  renderNotes();
  if (R.game.tree) setView("notes");
  $("save-report").hidden = false;
  $("save-annotated").hidden = false;
  if (res.side) $("review-side").value = res.side;
  renderMoments();
}

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
  $("viewtabs").hidden = !(tree && tree.annotated);   // shown for every reviewed game too
  $("view-table").hidden = true;
  setView(tree && tree.annotated ? "notes" : "moves");
  $("ask").hidden = false;
  const h = summary.headers;
  $("game-title").textContent = `${h.White || "?"} – ${h.Black || "?"}`;
  const bits = [h.Result, h.Event && h.Event !== "?" ? h.Event : "", h.Date && !h.Date.includes("?") ? h.Date : ""];
  const full = Math.ceil(summary.moves.length / 2);
  $("game-sub").textContent = bits.filter(Boolean).join(", ") + `. ${full} moves.`;
  $("review-controls").hidden = false;
  $("save-report").hidden = true;
  $("save-annotated").hidden = true;
  $("moments").hidden = true;
  $("review-side").value = summary.side || guessSide(h);
  setOrientation(($("review-side").value === "black") ? "black" : "white");
  renderMoveList();
  renderNotes();
  renderGraph();
  renderReviewBoard();
  renderCoach();
  renderAsk();
  if (summary.review) {               // reviewed before: show that review
    applyReview(summary.review);
    goTo(-1);
    const when = (summary.review.reviewed_at || "").replace("T", " ").slice(0, 16);
    toast(`Showing the review from ${when || "before"}. "Review game" redoes it.`, 5000);
  }
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
                turnColor: n.fen.split(" ")[1] === "w" ? "white" : "black",
                check: isCheck(n.san) ? (n.fen.split(" ")[1] === "w" ? "white" : "black") : false });
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
      check: checkedSide(mv),
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
  if (R.running && R.follow) {            // the player took over the board
    R.follow = false;
    $("follow-review").hidden = false;
  }
  R.cursor = Math.max(-1, Math.min(R.game.moves.length - 1, i));
  R.node = null;
  R.compare = false;
  R.facts = null;                    // set again by the move's facts panel
  renderMoveList();
  markNotes();
  markAnalysis();
  renderReviewBoard();
  renderCoach();
  renderMoments();
  renderAsk();
  $("coach-scroll").scrollTop = 0;   // a move opens on its comment
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
  $("coach-scroll").scrollTop = 0;
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
    html += renderAnnotation(text) + tacticsHtml(mv) + legendHtml(mv) + planHtml(mv);
    const removed = (Object.values(mv.stats || {})[0] || {}).removed || 0;
    if (removed) html += `<div class="checked" title="The coach's statements are checked against the board. A statement that was not true was taken out rather than shown.">${removed === 1 ? "One sentence was" : removed + " sentences were"} left out because ${removed === 1 ? "it was" : "they were"} not true on the board.</div>`;
    html += CHECKED_NOTE;
    html += factsHtml(mv);
  } else if (mv.reason && Object.values(mv.errors || {})[0]) {
    html += notesHtml(mv) + `<p class="coach-error">The coach could not write this comment: ${esc(Object.values(mv.errors)[0])}</p>`;
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
  const rv = n.main ? reviewedMove(n.ply) : null;
  const moveGlyph = n.nags.filter((g) => g.move).map((g) => g.glyph).join("")
    || (rv && rv.reason ? markOf(rv) : "");
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
  const coach = n.main ? coachNote(n.ply) : "";
  if (coach) html += `</p>${coach}<p class="line">`;
  return { html, broke: Boolean(c || coach) };
}

// The coach's comment on a reviewed move, for the Annotations pane: what the
// review's comment says, with the move's mark when the PGN gave it none.
function reviewedMove(ply) {
  return R.reviewed && ply >= 0 ? R.game.moves[ply] : null;
}
function coachNote(ply) {
  const mv = reviewedMove(ply);
  const text = mv && Object.values(mv.annotations || {})[0];
  if (!text || !mv.reason) return "";
  const mark = markOf(mv);
  const head = `${esc(JUDGMENT_TEXT[mv.label] || mv.label)}${mark ? ` ${esc(mark)}` : ""}`;
  return `<div class="cmt coach"><span class="by-coach">Coach · ${head}</span>${renderAnnotation(text)}</div>`;
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
  if (!tree || !(tree.annotated || R.reviewed)) { el.innerHTML = ""; return; }
  const intro = commentText(tree.nodes[0].comment);
  const h = R.game.headers;
  const by = [];
  if (tree.annotated) by.push(h.Annotator ? `Annotated by ${esc(h.Annotator)}` : "The PGN's annotations");
  if (R.reviewed) by.push(`${by.length ? "c" : "C"}omments by the coach from this review (${esc((R.game.models || [])[0] || "local model")})`);
  let html = by.length ? `<p class="by">${by.join("; ")}</p>` : "";
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
  $("analysis").hidden = v !== "table";
  markNotes();
  if (v === "table") renderAnalysis();
}

// ---- the analysis table (Lucas Chess's "All moves"): every move against
// Stockfish: its best move, what was lost, accuracy, and Stockfish's own
// win / draw / loss estimate for the side that moved.

const MARK_CLASS = { "!!": "brilliant", "!": "good", "!?": "interesting", "best": "best", "ok": "ok",
                     "?!": "inacc", "?": "mistake", "??": "blunder", "book": "book" };
R.tableSide = "all";

function renderAnalysis() {
  const el = $("analysis");
  if (!R.reviewed || !R.game) { el.innerHTML = ""; return; }
  const chip = (v, t) => `<button data-side="${v}" class="${R.tableSide === v ? "on" : ""}">${t}</button>`;
  let html = `<div class="an-head">${chip("all", "All moves")}${chip("white", "White")}${chip("black", "Black")}</div>`;
  html += `<table class="an-table"><thead><tr><th>Move</th><th>Best</th><th class="num" title="Pawns lost against the best move">Loss</th>`
    + `<th class="num">Acc.</th><th title="Stockfish's own win / draw / loss estimate for the side that moved, after the move. It models engine-against-engine play: a two-pawn edge reads as a near-certain win. For club play the winning chance (the graph, the accuracy) is the human measure.">SF win · draw · loss</th></tr></thead><tbody>`;
  for (const mv of R.game.moves) {
    if (R.tableSide !== "all" && mv.color !== R.tableSide) continue;
    const mark = markOf(mv);
    const cls = MARK_CLASS[mv.mark] || "";
    const best = mv.mark === "book" ? "" : (mv.best_san && mv.best_san !== mv.san ? figurine(mv.best_san) : "=");
    const loss = mv.cp_loss ? (mv.cp_loss / 100).toFixed(2) : "";
    const w = mv.wdl;
    const wdl = w ? `<span class="wdl sm" title="win ${w[0]}% · draw ${w[1]}% · loss ${w[2]}%">`
      + [["w", w[0]], ["d", w[1]], ["b", w[2]]].map(([c, x]) => x ? `<i class="${c}" style="width:${x}%">${x >= 22 ? Math.round(x) : ""}</i>` : "").join("")
      + `</span>` : "";
    html += `<tr data-ply="${mv.ply}" class="${mv.ply === R.cursor ? "cur" : ""}">`
      + `<td class="m ${cls}">${esc(figurine(mv.move_label))}<b>${esc(mark)}</b></td><td class="bm">${esc(best)}</td>`
      + `<td class="num">${loss}</td><td class="num">${mv.accuracy === null || mv.accuracy === undefined || mv.mark === "book" ? "" : Math.round(mv.accuracy)}</td>`
      + `<td>${wdl}</td></tr>`;
  }
  el.innerHTML = html + `</tbody></table>`;
  el.querySelectorAll(".an-head button").forEach((b) => b.addEventListener("click", () => { R.tableSide = b.dataset.side; renderAnalysis(); }));
  el.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => goTo(Number(tr.dataset.ply))));
  const cur = el.querySelector("tr.cur");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function markAnalysis() {
  const el = $("analysis");
  if (el.hidden) return;
  el.querySelectorAll("tr.cur").forEach((tr) => tr.classList.remove("cur"));
  const tr = el.querySelector(`tr[data-ply="${R.cursor}"]`);
  if (tr) { tr.classList.add("cur"); tr.scrollIntoView({ block: "nearest" }); }
}
document.querySelectorAll("#viewtabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

// ---- evaluation graph: our review's evaluation once there is one, else the
// annotator's engine (ChessBase %evp). White's winning chance per move;
// click to go to that move.

function evalSeries() {
  if (!R.game) return null;
  if (R.running && R.live && R.live.length > 1)
    return { src: "live", vals: R.game.moves.map((_, i) => (R.live[i + 1] ? winPctWhite(R.live[i + 1][0], R.live[i + 1][1]) : null)) };
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
  let path = "", started = false;
  s.vals.forEach((v, i) => {
    if (v === null) return;
    path += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    started = true;
  });
  // A line on a framed panel: up is White, down is Black. (The filled area
  // blended into the page; a line reads at any size, in both themes.)
  const ticks = R.reviewed ? R.game.moves.map((m, i) => (m.reason && BAD.has(m.label)
    ? `<line class="tick j-${m.label}" x1="${x(i).toFixed(1)}" y1="${H - 9}" x2="${x(i).toFixed(1)}" y2="${H}"/>` : "")).join("") : "";
  el.hidden = false;
  el.innerHTML = `<div class="g-head"><span>${s.src === "review" ? "Evaluation (Stockfish, this review)" : s.src === "live" ? "Evaluation (Stockfish, analysing…)" : "Evaluation (the annotator's engine)"}</span><span class="g-tip" id="g-tip"></span></div>`
    + `<div class="g-frame"><div class="g-axis"><span>White</span><span>Black</span></div><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="White's winning chance move by move">`
    + `<line class="mid" x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}"/>`
    + ticks + `<path class="ln" d="${path}"/>`
    + `<line class="cur" id="g-cur" x1="-10" y1="0" x2="-10" y2="${H}"/>`
    + `<line class="hov" id="g-hov" x1="-10" y1="0" x2="-10" y2="${H}"/></svg></div>`;
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

// Lucas Chess's Summary: how each side's moves were classed, and accuracy
// and best moves by phase.
const MARK_ROWS = [["!!", "Brilliant"], ["!", "Good"], ["!?", "Interesting"], ["best", "Best"],
                   ["ok", "Acceptable"], ["?!", "Inaccuracies"], ["?", "Mistakes"], ["??", "Blunders"],
                   ["book", "Book"]];
function statsHtml() {
  const st = R.game.stats;
  if (!st || !st.white) return "";
  const acc = R.game.accuracy || {};
  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x)}%`);
  let rows = `<tr class="hd"><th></th><th>White</th><th>Black</th></tr>`;
  rows += `<tr class="em"><th>Accuracy</th><td>${pct(acc.white)}</td><td>${pct(acc.black)}</td></tr>`;
  rows += `<tr><th>Best moves</th><td>${pct(st.white.best_pct)}</td><td>${pct(st.black.best_pct)}</td></tr>`;
  for (const [k, t] of MARK_ROWS) {
    const a = st.white.counts[k] || 0, b = st.black.counts[k] || 0;
    if (!a && !b && ["!!", "!", "!?", "book"].includes(k)) continue;     // praise only when earned
    const n = (x) => (x ? x : `<span class="zero">0</span>`);
    rows += `<tr class="${MARK_CLASS[k]}"><th>${["best", "ok", "book"].includes(k) ? "" : `<b>${esc(k)}</b> `}${t}</th><td>${n(a)}</td><td>${n(b)}</td></tr>`;
  }
  let ph = `<tr class="hd"><th>By phase</th><th>White</th><th>Black</th></tr>`;
  for (const p of ["opening", "middlegame", "endgame"]) {
    const w = st.white.phases[p], b = st.black.phases[p];
    if (!w.moves && !b.moves) continue;
    const cell = (x) => (x.moves ? `${pct(x.accuracy)} <span class="sub">· best ${pct(x.best_pct)} · ${x.moves}</span>` : "–");
    ph += `<tr><th>${p[0].toUpperCase() + p.slice(1)}</th><td>${cell(w)}</td><td>${cell(b)}</td></tr>`;
  }
  return `<div class="stats"><table class="st-table">${rows}</table>`
    + `<table class="st-table phase" title="Accuracy · share of the engine's first choice · moves">${ph}</table></div>`;
}

function renderSummary() {
  const sum = R.game.summary || [];
  const pats = R.game.patterns || [];
  if (!sum.length && !pats.length) return "";
  let html = `<section class="summary" id="summary"><h3>Game summary</h3>${statsHtml()}<ul>`;
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
    if (!(a.removed && a.removed.length)) meta.push("Piece facts checked against the board; judgements are the model's.");
    if (a.secs) meta.push(`${a.secs}s`);
    if (meta.length) html += `<p class="meta">${esc(meta.join(" · "))}</p>`;
  }
  el.innerHTML = html;
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
  showLatestAnswer();
  const res = question ? await call("ask_coach", ply, question)
                       : await call("explain_position", ply);
  R.asking = false;
  if (res) Object.assign(entry, res, { pending: false });
  else R.answers[ply] = R.answers[ply].filter((a) => a !== entry);
  if (R.cursor === ply) { renderAsk(); showLatestAnswer(); }
  else { $("explain-btn").disabled = false; $("ask-btn").disabled = false; }
}

// The newest question at the top of the view; the comment is above it, one
// scroll away, and comes back first when you return to this move.
function showLatestAnswer() {
  const qs = $("ask-answer").querySelectorAll(".q");
  if (qs.length) qs[qs.length - 1].scrollIntoView({ block: "start", behavior: "smooth" });
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
// While a review runs, the game and the settings stay as they are: the
// result belongs to the game it was started on.
function lockForReview(on) {
  for (const id of ["start-review", "game-pick", "open-pgn", "open-settings", "review-played", "review-side"])
    $(id).disabled = on;
}

async function startReview() {
  const res = await call("start_review", $("review-side").value, false);
  if (!res) return;
  lockForReview(true);
  R.running = true; R.follow = true; R.live = []; R.liveShown = null;
  $("follow-review").hidden = true;
  $("coach").innerHTML = `<p class="empty">Stockfish is starting…</p>`;
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
    const pct = s.stage === "engine" ? frac * 40 : s.stage === "prepare" ? 40 : 40 + frac * 60;
    $("progress-fill").style.width = `${pct}%`;
    // How long this one step has taken, once it is more than a few seconds:
    // a slow step then reads as slow, not as a hang.
    const step = `${s.stage}:${s.done}:${s.ply}:${s.message}`;
    if (R.step !== step) { R.step = step; R.stepSince = Date.now(); }
    const secs = Math.round((Date.now() - R.stepSince) / 1000);
    const took = secs >= 4 ? ` · ${secs}s` : "";
    $("progress-text").textContent = (s.stage === "engine" && s.message.startsWith("Analysing")
      ? `Analysing position ${Math.min(s.done + (s.searching ? 1 : 0), s.total)} of ${s.total}` : s.message) + took;
    showLive(s);
    return;
  }
  clearInterval(R.polling);
  lockForReview(false);
  R.running = false;
  $("follow-review").hidden = true;
  $("progress").hidden = true;
  if (s.state === "finished") {
    const res = s.result;
    if (res.key !== undefined && R.game.key !== undefined && res.key !== R.game.key) {
      toast("That review was for another game; it was not applied.", 5000);
      return;
    }
    applyReview(res);
    const n = res.moves.filter((m) => m.reason).length;
    toast(`Review complete: ${n} critical moments.${res.saved_to ? ` Kept in your games (${res.saved_to}).` : ""}`, 4500);
    goTo(-1);                       // open on the game summary
  } else {
    // stopped or failed: back to the game as it was, without the live graph
    R.live = null;
    renderGraph();
    goTo(R.cursor);
    if (s.state === "error") toast(s.message, 6000);
    else if (s.state === "cancelled") toast("Review stopped.");
  }
}

$("cancel-review").addEventListener("click", () => api().cancel_review());
$("follow-review").addEventListener("click", () => {
  R.follow = true;
  R.liveShown = null;
  $("follow-review").hidden = true;
});

// While a review runs, the board follows it: each position as Stockfish
// analyses it (eval bar, move list and graph filling in), then each critical
// moment while the coach writes about it. Clicking a move takes the board
// back; "Follow" returns it to the analysis.
function showLive(s) {
  if (s.live && s.live.length !== (R.live || []).length) {
    R.live = s.live;
    renderGraph();
  }
  if (s.stage === "prepare") {
    $("coach").innerHTML = `<p class="live">${esc(s.message)}…</p>`
      + `<p class="empty">Stockfish has analysed every position. Next, the coach writes about the moments that decided the game.</p>`;
    R.liveShown = null;
    return;
  }
  if (!R.follow || s.ply === null || s.ply === undefined || !R.game) return;
  const pos = s.stage === "engine" ? s.ply : s.ply + 1;          // position index, 0 = start
  const key = `${s.stage}:${pos}:${s.searching ? "s" : "d"}`;
  if (R.liveShown === key) return;
  R.liveShown = key;
  const mv = pos > 0 ? R.game.moves[pos - 1] : null;
  R.cursor = pos - 1;
  renderMoveList();
  markNotes();                       // the Annotations pane follows too
  markGraph();
  if (tab === "review") {
    board.set({ fen: mv ? mv.fen_after : R.game.start_fen, lastMove: mv ? sqs(mv.uci) : undefined,
                turnColor: mv ? other(mv.color) : "white",
                check: mv ? checkedSide(mv) : false, movable: { color: undefined, dests: new Map() } });
    board.setAutoShapes([]);
    const sc = s.stage === "engine" && !s.searching ? R.live[pos] : null;
    if (sc) setEval(winPctWhite(sc[0], sc[1]));
  }
  const where = mv ? figurine(mv.move_label) : "the starting position";
  $("coach").innerHTML = s.stage === "engine"
    ? `<p class="live">Stockfish is analysing <b>${esc(where)}</b>${!s.searching && s.verdict ? `: ${esc(s.verdict)}` : "…"}</p>`
      + `<p class="empty">Position ${Math.min(s.done + (s.searching ? 1 : 0), s.total)} of ${s.total}. Next, the coach writes about the moments that decided the game.</p>`
    : `<p class="live">The coach is writing about <b>${esc(where)}</b> (moment ${s.done + 1} of ${s.total}).</p>`
      + `<p class="empty">Its comment is checked against the board before you see it.</p>`;
  $("coach-scroll").scrollTop = 0;
}
$("save-annotated").addEventListener("click", async () => {
  const res = await call("save_annotated_pgn_dialog");
  if (res && res.saved) toast(`Saved ${res.saved}`);
});
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
  if (st.saved_to) toast(`Game kept in your games (${st.saved_to}).`);
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
                         "comment_language", "library_dir", "max_seconds"];
const SETTINGS_CHECKS = ["autosave"];
$("open-settings").addEventListener("click", async () => {
  const s = await call("get_settings");
  if (!s) return;
  settingsCache = s;
  const f = $("settings-form");
  for (const k of SETTINGS_FIELDS) f.elements[k].value = s[k] ?? "";
  for (const k of SETTINGS_CHECKS) f.elements[k].checked = !!s[k];
  $("settings-file").textContent = `Saved to ${s.config_file}`;
  $("settings-error").hidden = true;
  $("settings").showModal();
});
$("settings-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;   // let the dialog close
  e.preventDefault();
  const f = e.target;
  const values = Object.fromEntries(SETTINGS_FIELDS.map((k) => [k, f.elements[k].value.trim()]));
  for (const k of SETTINGS_CHECKS) values[k] = f.elements[k].checked;
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
    const pane = !$("annotations").hidden ? $("annotations") : !$("analysis").hidden ? $("analysis") : $("movelist");
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
  const record = p.tries ? ` · solved ${p.solved} of ${p.tries} ${p.tries === 1 ? "try" : "tries"}` : "";
  const why = d.queue ? ` · ${d.queue}` : "";           // all games: why it is in this place
  $("drill-count").textContent = `Exercise ${X.i + 1} of ${X.drills.length}${where}${why}${record}`;
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

// ---------------------------------------------------------------- games
//
// The library: ordinary PGN files (Played.pgn, Reviewed.pgn and any you
// add) with an index. Double-click or Open puts a game in the Review tab
// (with its review, if it was reviewed before). The explorer reads the
// same games' openings.

const G = { view: "list", sort: "date", desc: true, selected: null, rows: [], line: [], ex: null,
            armed: false, timer: null };

document.querySelectorAll("#lib-views button").forEach((b) => b.addEventListener("click", () => {
  G.view = b.dataset.lview;
  document.querySelectorAll("#lib-views button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  openGames();
}));
for (const id of ["lib-file", "lib-mine", "lib-result", "lib-reviewed"])
  $(id).addEventListener("change", () => openGames());
$("lib-q").addEventListener("input", () => { clearTimeout(G.timer); G.timer = setTimeout(openGames, 250); });
document.querySelectorAll(".lib-table:not(.ex-table) th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  G.desc = G.sort === th.dataset.sort ? !G.desc : th.dataset.sort === "date";
  G.sort = th.dataset.sort;
  document.querySelectorAll(".lib-table th[data-sort]").forEach((x) => x.classList.remove("sorted", "desc"));
  th.classList.add("sorted");
  if (G.desc) th.classList.add("desc");
  openGames();
}));
$("lib-add").addEventListener("click", async () => {
  const res = await call("library_add_dialog");
  if (res && res.file) {
    toast(`Added ${res.games} game${res.games === 1 ? "" : "s"} as ${res.file}.`);
    $("lib-file").value = "";
    openGames();
  }
});
$("lib-open").addEventListener("click", () => G.selected !== null && openFromLibrary(G.selected));
$("lib-remove").addEventListener("click", async () => {
  if (G.selected === null) return;
  // two clicks, no dialog: the first arms it, the second removes
  if (!G.armed) {
    G.armed = true;
    $("lib-remove").textContent = "Click again to remove";
    setTimeout(() => { G.armed = false; $("lib-remove").textContent = "Remove from file…"; }, 3000);
    return;
  }
  G.armed = false;
  $("lib-remove").textContent = "Remove from file…";
  const res = await call("library_remove", G.selected);
  if (res) { G.selected = null; toast("Removed."); openGames(); }
});
$("ex-start").addEventListener("click", () => { G.line = []; explore(); });
$("ex-back").addEventListener("click", () => { G.line.pop(); explore(); });

function openGames() {
  $("lib-list").hidden = G.view !== "list";
  $("lib-explorer").hidden = G.view !== "explorer";
  $("lib-list-filters").hidden = G.view !== "list";
  if (G.view === "explorer") return explore();
  listGames();
}

async function listGames() {
  const res = await call("library_list", {
    text: $("lib-q").value, file: $("lib-file").value, mine: $("lib-mine").value,
    result: $("lib-result").value, reviewed: $("lib-reviewed").checked, sort: G.sort, desc: G.desc });
  if (!res) return;
  fillFiles(res.files);
  G.rows = res.games;
  $("lib-path").textContent = `Folder: ${res.folder}`;
  $("lib-path").title = res.folder;
  const meName = (res.me || "").toLowerCase();
  $("lib-rows").innerHTML = res.games.map((g) => {
    const me = (n) => (meName && n.toLowerCase() === meName ? " me" : "");
    const opening = g.eco || g.opening ? `${esc(g.eco)} ${esc(g.opening)}`.trim() : "";
    return `<tr data-id="${g.id}" class="${g.id === G.selected ? "sel" : ""}" title="${esc(g.event)} · ${esc(g.file)}">`
      + `<td class="${me(g.white)}">${esc(g.white)}</td><td class="${me(g.black)}">${esc(g.black)}</td>`
      + `<td>${esc(g.result === "1/2-1/2" ? "½-½" : g.result)}</td><td>${esc(g.date.replace(/\.\?\?/g, ""))}</td>`
      + `<td class="op" title="${opening}">${opening}</td><td class="num">${Math.ceil(g.plies / 2)}</td>`
      + `<td>${g.reviewed ? "✓" : ""}</td></tr>`;
  }).join("");
  $("lib-rows").querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => selectRow(Number(tr.dataset.id)));
    tr.addEventListener("dblclick", () => openFromLibrary(Number(tr.dataset.id)));
  });
  const shown = res.games.length;
  $("lib-count").textContent = res.total ? `${shown < res.total ? `${shown} of ` : ""}${res.total} game${res.total === 1 ? "" : "s"}` : "";
  $("lib-empty").hidden = res.total > 0;
  $("lib-empty").textContent = res.files.length
    ? "No games match." : "No games yet. Play or review a game, or add a PGN file.";
  selectRow(res.games.some((g) => g.id === G.selected) ? G.selected : null);
}

function fillFiles(files) {
  const sel = $("lib-file"), cur = sel.value;
  sel.innerHTML = `<option value="">All files</option>` + files.map((f) => `<option>${esc(f)}</option>`).join("");
  sel.value = files.includes(cur) ? cur : "";
}

function selectRow(id) {
  G.selected = id;
  $("lib-rows").querySelectorAll("tr").forEach((tr) => tr.classList.toggle("sel", Number(tr.dataset.id) === id));
  $("lib-open").disabled = id === null;
  $("lib-remove").disabled = id === null;
}

async function openFromLibrary(id) {
  const res = await call("library_open", id);
  if (!res) return;
  showTab("review");
  loadSummary(res);
}

async function explore() {
  const res = await call("library_explore", G.line, $("lib-mine").value, $("lib-file").value);
  if (!res) { G.line.pop(); return; }
  fillFiles(res.files || []);
  G.ex = res;
  $("ex-line").textContent = res.line || "Starting position";
  $("ex-back").disabled = $("ex-start").disabled = !G.line.length;
  const mine = $("lib-mine").value;
  $("ex-score-h").textContent = mine === "white" || mine === "black" ? "Your score"
    : `${res.turn === "white" ? "White" : "Black"}'s score`;
  $("ex-rows").innerHTML = res.moves.map((m) => {
    const t = m.white + m.draws + m.black || 1;
    const seg = (cls, k) => {
      const pct = Math.round(100 * k / t);
      return k ? `<i class="${cls}" style="width:${100 * k / t}%">${pct >= 18 ? pct + "%" : ""}</i>` : "";
    };
    const bar = `<span class="wdl">${seg("w", m.white)}${seg("d", m.draws)}${seg("b", m.black)}</span>`;
    return `<tr data-uci="${m.uci}"><td class="mv-san">${esc(figurine(m.san))}</td><td class="num">${m.games}</td>`
      + `<td title="${m.white} · ${m.draws} · ${m.black}">${bar}</td><td class="num">${m.score === null ? "–" : m.score + "%"}</td></tr>`;
  }).join("");
  $("ex-rows").querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => {
    G.line.push(tr.dataset.uci);
    explore();
  }));
  $("ex-empty").hidden = res.moves.length > 0;
  $("ex-empty").textContent = res.games === 0 && !G.line.length
    ? (mine ? "None of your games yet: set your name in Settings, as it appears in your PGNs." : "No games yet.")
    : "No game in your library reached this position.";
  renderGamesBoard();
}

function renderGamesBoard() {
  if (tab !== "games") return;
  renderPlayers();
  setEval(50);
  board.setAutoShapes([]);
  if (G.view !== "explorer" || !G.ex) {
    board.set({ fen: START, lastMove: undefined, check: false, movable: { color: undefined, dests: new Map() } });
    return;
  }
  const last = G.line[G.line.length - 1];
  board.set({
    fen: G.ex.fen, turnColor: G.ex.turn, lastMove: last ? sqs(last) : undefined, check: false,
    movable: { free: false, color: G.ex.turn, dests: new Map(Object.entries(G.ex.dests)), showDests: true,
               events: { after: (o, d) => { G.line.push(o + d + (isPromotion(o, d) ? "q" : "")); explore(); } } },
  });
  // the most played moves as arrows
  board.setAutoShapes(G.ex.moves.slice(0, 3).map((m, i) => ({ orig: m.uci.slice(0, 2), dest: m.uci.slice(2, 4), brush: i ? "played" : "defend" })));
}

function isPromotion(o, d) {
  const p = board.state.pieces.get(d);
  return p && p.role === "pawn" && (d[1] === "8" || d[1] === "1");
}

// ---------------------------------------------------------------- start

async function init() {
  settingsCache = await api().get_settings();
  if (settingsCache && settingsCache.version) {
    $("app-version").textContent = `v${settingsCache.version}`;
    document.title = `chesscoach ${settingsCache.version}`;
  }
  if (settingsCache && !settingsCache.error && !settingsCache.stockfish_path) {
    toast("Set the Stockfish path in Settings before reviewing a game.", 6000);
  }
  renderCoach();
  showTab("review");
}
if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init, { once: true });
