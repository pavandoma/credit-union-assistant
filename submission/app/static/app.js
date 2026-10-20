"use strict";
/* Credit Union Assistant chat UI. All server text is inserted with textContent (never innerHTML). */
(function () {
  const $ = (id) => document.getElementById(id);
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December"];
  // memory: the last question as the server read it; sent with the next message for follow-ups
  const state = { ready: false, status: null, busy: false, memory: null, snapDates: {} };

  const SAMPLES = [
    ["Rates", [
      "What's the APR for a 60-month auto loan on a 2025 model-year vehicle?",
      "What is the APY on the 17-month special certificate?",
      "What dividend rate do I earn on Share Savings with a $15,000 balance?",
      "What are the mortgage rates right now?",
    ]],
    ["Follow-ups (after the auto loan question)", [
      "What about 72 months?",
      "And for a 2024 vehicle?",
    ]],
    ["Dates and changes", [
      "What was the rate on the 10-month fixed jumbo special certificate as of May 2026?",
      "Did the 10 month fixed jumbo special certificate rate change?",
    ]],
    ["Branches and service", [
      "What is the address of the Galena Branch?",
      "What are the drive-up hours?",
      "What is Dupaco's routing number?",
    ]],
    ["Not on the website", [
      "What is your rate on a 7/1 adjustable-rate mortgage?",
    ]],
  ];
  const STARTERS = [SAMPLES[0][1][0], SAMPLES[3][1][0], SAMPLES[2][1][1]];

  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children) if (c !== null && c !== undefined) n.append(c instanceof Node ? c : String(c));
    return n;
  }

  function safeUrl(u) {
    try {
      const x = new URL(String(u));
      return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
    } catch (e) { return null; }
  }
  const shortUrl = (u) => u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
  const linkEl = (url, text) => el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: text || shortUrl(url) });

  /* ---------- dates: always the full form, for example "September 28, 2026" ---------- */
  function fmtDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return iso ? String(iso) : "";
    const mo = Number(m[2]), d = Number(m[3]);
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return String(iso);
    return `${MONTHS[mo - 1]} ${d}, ${m[1]}`;
  }
  const isoOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  // ISO dates (yyyy-mm-dd) in answers, reasons and the agents' "[effective ...]" notes
  const longDates = (text) => String(text || "").replace(/\b\d{4}-\d{2}-\d{2}\b/g, (iso) => fmtDate(iso));
  const copyLabel = (sid) => state.snapDates[sid] ? "Copy of " + fmtDate(state.snapDates[sid]) : (sid || "");

  /* ---------- theme ---------- */
  function setTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    try { t ? localStorage.setItem("theme", t) : localStorage.removeItem("theme"); } catch (e) { /* storage blocked */ }
  }
  try { const saved = localStorage.getItem("theme"); if (saved === "dark" || saved === "light") setTheme(saved); } catch (e) { /* ignore */ }
  $("theme-btn").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(cur === "dark" ? "light" : "dark");
  });

  /* ---------- tabs ---------- */
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  function selectTab(tab, focus) {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on || !state.ready;
    });
    if (focus) tab.focus();
    if (state.ready && window.scrollY > 0) window.scrollTo(0, 0);
    // the timeline is drawn at the width of its panel, so it is drawn when the panel is shown
    if (state.ready && tab.id === "tab-snapshots") renderTimeline(state.status);
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t));
    t.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const j = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
        selectTab(tabs[j], true);
      }
    });
  });
  const currentTab = () => tabs.find((t) => t.getAttribute("aria-selected") === "true") || tabs[0];

  /* ---------- loading ---------- */
  async function poll() {
    let s;
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      s = await r.json();
    } catch (e) {
      $("loading-step").textContent = "Cannot reach the server. Is it still running?";
      setTimeout(poll, 2000);
      return;
    }
    state.status = s;
    if (s.error) {
      $("status-pill").textContent = "Error";
      $("status-pill").className = "pill bad";
      $("loading-step").textContent = s.error;
      return;
    }
    if (!s.ready) {
      const pct = s.total ? Math.max(3, Math.round(100 * s.done / s.total)) : 3;
      $("bar-fill").style.width = pct + "%";
      $("bar").setAttribute("aria-valuenow", String(pct));
      $("loading-step").textContent = s.step + (s.total && s.done ? ` (${s.done} of ${s.total})` : "");
      $("status-pill").textContent = "Loading";
      setTimeout(poll, 500);
      return;
    }
    onReady(s);
  }

  function onReady(s) {
    state.ready = true;
    state.snapDates = Object.fromEntries(s.snapshots.map((x) => [x.snapshot_id, x.date]));
    $("loading").hidden = true;
    $("status-pill").textContent = "Ready";
    $("status-pill").className = "pill ready";
    $("coverage").textContent = `${s.snapshots.length} website copies, ${fmtDate(s.first_date)} to ${fmtDate(s.latest_date)}`;
    buildAsOf(s);
    renderSnapshots(s);
    renderSamples();
    newChat();
    selectTab(currentTab());
  }

  /* ---------- "As of" menu ---------- */
  function buildAsOf(s) {
    const sel = $("as-of");
    sel.replaceChildren(el("option", { value: "", text: "Current (" + fmtDate(s.latest_date) + ")" }));
    s.snapshots.slice(0, -1).reverse().forEach((snap) => sel.append(el("option", { value: snap.date, text: fmtDate(snap.date) })));
    sel.append(el("option", { value: "custom", text: "Another date…" }));

    const today = new Date();
    const first = Number(String(s.first_date).slice(0, 4)) || today.getFullYear();
    const last = Math.max(Number(String(s.latest_date).slice(0, 4)) || 0, today.getFullYear());
    $("d-year").replaceChildren(...Array.from({ length: last - first + 2 }, (_, i) => {
      const y = String(first - 1 + i);
      return el("option", { value: y, text: y });
    }));
    $("d-month").replaceChildren(...MONTHS.map((m, i) => el("option", { value: String(i + 1), text: m })));
    // "Another date" starts at today's date
    $("d-year").value = String(today.getFullYear());
    $("d-month").value = String(today.getMonth() + 1);
    fillDays(today.getDate());
    updateAsOf();
  }
  function fillDays(keep) {
    const y = Number($("d-year").value), m = Number($("d-month").value);
    const n = new Date(y, m, 0).getDate();
    const want = Math.min(keep || Number($("d-day").value) || 1, n);
    $("d-day").replaceChildren(...Array.from({ length: n }, (_, i) => el("option", { value: String(i + 1), text: String(i + 1) })));
    $("d-day").value = String(want);
  }
  function asOfValue() {
    const v = $("as-of").value;
    if (v !== "custom") return v;
    return `${$("d-year").value}-${String($("d-month").value).padStart(2, "0")}-${String($("d-day").value).padStart(2, "0")}`;
  }
  function copyFor(iso) {
    let best = null;
    for (const snap of state.status.snapshots) if (snap.date <= iso) best = snap.date;
    return best;
  }
  function updateAsOf() {
    const custom = $("as-of").value === "custom";
    $("custom-date").hidden = !custom;
    const iso = asOfValue();
    const s = state.status;
    let note;
    if (!iso) note = `Uses the newest copy. Today is ${fmtDate(isoOf(new Date()))}.`;
    else {
      const used = copyFor(iso);
      note = used ? `Uses the copy of ${fmtDate(used)}.` : `Before the first copy (${fmtDate(s.first_date)}). The answer will be "I don't know".`;
    }
    $("asof-note").textContent = note;
  }
  $("as-of").addEventListener("change", updateAsOf);
  $("d-month").addEventListener("change", () => { fillDays(); updateAsOf(); });
  $("d-year").addEventListener("change", () => { fillDays(); updateAsOf(); });
  $("d-day").addEventListener("change", updateAsOf);

  /* ---------- chat thread ---------- */
  const thread = $("thread");
  function scrollToEl(node) {
    thread.scrollTop = Math.max(0, node.offsetTop - 12);
  }
  function scrollToEnd() { thread.scrollTop = thread.scrollHeight; }

  function welcome() {
    const s = state.status;
    const chips = el("div", { class: "starter" });
    for (const q of STARTERS) chips.append(el("button", { type: "button", class: "chip", text: q, onclick: () => ask(q) }));
    return el("div", { class: "msg bot welcome" }, el("div", { class: "bubble" },
      el("p", { text: "Hi. Ask me about the credit union's rates, fees, products or branches." }),
      el("p", { text: `I answer only from the website copies of ${fmtDate(s.first_date)} to ${fmtDate(s.latest_date)}, and I show the sources for each answer. You can ask a short follow-up, for example "What about 72 months?".` }),
      chips));
  }
  function newChat() {
    state.memory = null;
    thread.replaceChildren(welcome());
    $("form-error").textContent = "";
    $("question").focus();
  }
  $("new-chat").addEventListener("click", newChat);

  function addUser(q, asOf) {
    const bubble = el("div", { class: "bubble" }, el("p", { text: q }));
    if (asOf) bubble.append(el("p", { class: "msg-meta", text: "As of " + fmtDate(asOf) }));
    const msg = el("div", { class: "msg user" }, bubble);
    thread.append(msg);
    scrollToEnd();
    return msg;
  }
  function addPending() {
    const msg = el("div", { class: "msg bot pending" }, el("div", { class: "bubble" },
      el("span", { class: "dots", role: "img", "aria-label": "Looking for the answer" }, el("i"), el("i"), el("i"))));
    thread.append(msg);
    scrollToEnd();
    return msg;
  }
  function addError(text) {
    thread.append(el("div", { class: "msg bot error-msg" }, el("div", { class: "bubble" }, el("p", { text }))));
    scrollToEnd();
  }

  // one answer line: links become anchors, ISO dates become full dates
  function renderLine(line, i) {
    const m = line.match(/^Link:\s*(\S+)$/);
    if (m && safeUrl(m[1])) return el("p", { class: "line link" }, "Link: ", linkEl(safeUrl(m[1]), m[1]));
    const p = el("p", { class: "line" + (i === 0 ? " first" : "") });
    for (const part of line.split(/(https?:\/\/[^\s<>"']*[^\s<>"'.,;:)\]])/)) {
      if (!part) continue;
      const url = /^https?:\/\//.test(part) ? safeUrl(part) : null;
      p.append(url ? linkEl(url, part) : longDates(part));
    }
    return p;
  }

  function sourcesBox(d) {
    const srcs = (d.sources || []).filter((s) => safeUrl(s.url));
    const others = d.other_values || [];
    const note = d.conflict_note;
    if (!srcs.length && !others.length && !note) return null;
    const sum = [srcs.length + (srcs.length === 1 ? " page" : " pages")];
    if (others.length) sum.push("other values on the website");
    const box = el("details", { class: "src-box" },
      el("summary", {}, el("span", { class: "src-title", text: "Sources" }), el("span", { class: "src-sum", text: sum.join(" · ") })));

    if (srcs.length) {
      const ol = el("ol", { class: "src-list" });
      for (const s of srcs) {
        ol.append(el("li", {},
          linkEl(safeUrl(s.url)),
          s.snapshot_id ? el("span", { class: "sid", text: copyLabel(s.snapshot_id) }) : null,
          s.quote ? el("blockquote", { text: longDates(s.quote) }) : null));
      }
      box.append(ol);
    }
    if (note) {
      box.append(el("div", { class: "src-sec" }, el("h4", { text: "Why this value" }), el("p", { text: longDates(note) })));
    }
    if (others.length) {
      const sec = el("div", { class: "src-sec" }, el("h4", { text: "Other values on the website" }),
        el("p", { class: "hint", text: "Different pages state different values for this fact. The chosen value is marked." }));
      for (const c of others) {
        const vals = el("div", { class: "vals" });
        for (const v of c.values || []) {
          const win = c.resolution && v === c.resolution;
          vals.append(el("span", { class: "val" + (win ? " win" : c.resolution ? "" : " open"), text: v, title: win ? "Chosen value" : c.resolution ? "Not chosen" : "Not resolved" }));
        }
        const links = el("ul", { class: "c-src" });
        for (const s of c.sources || []) {
          const url = safeUrl(s.url);
          if (url) links.append(el("li", {}, linkEl(url), s.quote ? el("blockquote", { text: longDates(s.quote) }) : null));
        }
        sec.append(el("div", { class: "conflict" },
          el("p", { class: "c-topic", text: c.topic }), vals,
          el("p", { class: "reason", text: c.resolution ? "Chosen: " + c.resolution + ". " + longDates(c.reason || "") : longDates(c.reason || "Not resolved.") }),
          links.children.length ? links : null));
      }
      box.append(sec);
    }
    box.append(el("p", { class: "hint", text: "Links open the live website, which can differ from the saved copy. The quotes show the saved text." }));
    return box;
  }

  function addAnswer(d) {
    const bubble = el("div", { class: "bubble" },
      el("span", { class: "badge" + (d.confident ? "" : " unknown"), text: d.confident ? "From the website" : "Not stated on the website" }));
    if (d.resolved) bubble.append(el("p", { class: "readas" }, el("span", { class: "k", text: "Read as: " }), d.resolved));
    String(d.text || "").split("\n").filter((x) => x.trim()).forEach((line, i) => bubble.append(renderLine(line, i)));
    const meta = [];
    if (d.snapshot_date) meta.push("Website copy of " + fmtDate(d.snapshot_date) + (d.snapshot_date === d.latest_date ? " (current)" : ""));
    if (typeof d.elapsed_ms === "number") meta.push(d.elapsed_ms + " ms");
    if (meta.length) bubble.append(el("p", { class: "msg-meta", text: meta.join(" · ") }));
    const msg = el("div", { class: "msg bot" + (d.confident ? "" : " unknown") }, bubble, sourcesBox(d));
    thread.append(msg);
    return msg;
  }

  /* ---------- composer ---------- */
  const box = $("question");
  function autosize() {
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 160) + "px";
  }
  box.addEventListener("input", () => { $("form-error").textContent = ""; autosize(); });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      e.preventDefault(); selectTab($("tab-chat")); box.focus();
    }
  });

  async function ask(text) {
    if (!state.ready || state.busy) return;
    const question = (text !== undefined ? text : box.value).trim();
    if (!question) { $("form-error").textContent = "Type a question first."; box.focus(); return; }
    if (question.length > (state.status.max_question || 1000)) { $("form-error").textContent = "The question is too long. Use 1,000 characters or fewer."; return; }
    const asOf = asOfValue();
    if (asOf && !/^\d{4}-\d{2}-\d{2}$/.test(asOf)) { $("form-error").textContent = "Select a full date, or select Current."; return; }
    $("form-error").textContent = "";
    if (text === undefined) { box.value = ""; autosize(); }
    selectTab($("tab-chat"));
    const userMsg = addUser(question, asOf);
    const pending = addPending();
    state.busy = true;
    $("ask-btn").disabled = true;
    try {
      const r = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, as_of: asOf || null, previous: state.memory }),
      });
      const data = await r.json().catch(() => ({}));
      pending.remove();
      if (!r.ok) { addError(data.error || "The question could not be answered. Try again."); return; }
      addAnswer(data);
      state.memory = data.resolved || data.question;
      scrollToEl(userMsg);
    } catch (e) {
      pending.remove();
      addError("Cannot reach the server. Is it still running?");
    } finally {
      state.busy = false;
      $("ask-btn").disabled = false;
      box.focus();
    }
  }

  // example buttons on the Features tab ask their question in the chat
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-ask]");
    if (b) ask(b.getAttribute("data-ask"));
  });

  function renderSamples() {
    const side = $("samples");
    side.replaceChildren();
    for (const [group, qs] of SAMPLES) {
      side.append(el("div", { class: "group", text: group }));
      for (const q of qs) side.append(el("button", { type: "button", class: "sample", text: q, onclick: () => ask(q) }));
    }
  }

  /* ---------- snapshots ---------- */
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, text) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const dayNum = (iso) => Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 864e5;

  // the copies on a time line to scale, with today's date; the gap after the newest copy is shaded
  function renderTimeline(s) {
    const svg = $("timeline");
    const W = Math.max(640, svg.parentElement.clientWidth), H = 124, padL = 64, padR = 72, axisY = 54;
    const today = isoOf(new Date());
    const first = s.first_date, last = s.latest_date;
    const end = today > last ? today : last;
    const lo = Date.UTC(Number(first.slice(0, 4)), Number(first.slice(5, 7)) - 1, 1) / 864e5;
    const hi = Date.UTC(Number(end.slice(0, 4)), Number(end.slice(5, 7)), 1) / 864e5;   // first day after the end month
    const X = (iso) => padL + (dayNum(iso) - lo) / (hi - lo) * (W - padL - padR);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.replaceChildren();

    if (today > last) svg.append(svgEl("rect", { class: "tl-gap", x: X(last), y: axisY - 9, width: X(today) - X(last), height: 18, rx: 4 }));
    svg.append(svgEl("line", { class: "tl-axis", x1: padL, x2: W - padR, y1: axisY, y2: axisY }));
    for (let y = Number(first.slice(0, 4)), m = Number(first.slice(5, 7)); ; m === 12 ? (m = 1, y++) : m++) {
      const start = `${y}-${String(m).padStart(2, "0")}-01`;
      if (dayNum(start) >= hi) break;
      svg.append(svgEl("line", { class: "tl-tick", x1: X(start), x2: X(start), y1: axisY - 5, y2: axisY + 5 }));
      const mid = `${y}-${String(m).padStart(2, "0")}-15`;
      svg.append(svgEl("text", { class: "tl-month", x: X(mid), y: H - 6, "text-anchor": "middle" },
        MONTHS[m - 1] + (start.slice(0, 7) === first.slice(0, 7) || m === 1 ? " " + y : "")));
    }
    // labels take turns above and below the line, so copies one week apart do not overlap
    s.snapshots.forEach((snap, i) => {
      const x = X(snap.date), latest = i === s.snapshots.length - 1, above = i % 2 === 0;
      svg.append(svgEl("line", { class: "tl-stem", x1: x, x2: x, y1: above ? 26 : axisY + 8, y2: above ? axisY - 8 : 80 }));
      svg.append(svgEl("circle", { class: "tl-dot" + (latest ? " latest" : ""), cx: x, cy: axisY, r: 6 }));
      svg.append(svgEl("text", { class: "tl-label" + (latest ? " strong" : ""), x, y: above ? 20 : 94, "text-anchor": "middle" },
        fmtDate(snap.date) + (latest ? " (current)" : "")));
    });
    if (today > last) {
      const x = X(today);
      svg.append(svgEl("line", { class: "tl-today", x1: x, x2: x, y1: axisY - 14, y2: 80 }));
      svg.append(svgEl("text", { class: "tl-today-label", x, y: 94, "text-anchor": "middle" }, "Today, " + fmtDate(today)));
    }
    svg.setAttribute("aria-label", `Timeline: ${s.snapshots.length} website copies from ${fmtDate(first)} to ${fmtDate(last)}; today is ${fmtDate(today)}.`);
    $("timeline-note").textContent = today > last
      ? `The shaded part is the time after the newest copy (${fmtDate(last)}). Answers cannot include changes made after that date.`
      : "The newest copy is from today.";
  }
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.ready && !$("panel-snapshots").hidden) renderTimeline(state.status); }, 150);
  });

  function renderBars(s) {
    const max = Math.max(1, ...s.snapshots.map((x) => Number(x.pages)));
    const box = $("page-bars");
    box.replaceChildren();
    s.snapshots.forEach((snap, i) => {
      const latest = i === s.snapshots.length - 1;
      const all = Number(snap.pages), changed = Math.min(all, Number(snap.new_or_changed)), same = all - changed;
      const segNew = el("span", { class: "seg new" }), segSame = el("span", { class: "seg same" });
      segNew.style.width = (100 * changed / max) + "%";
      segSame.style.width = (100 * same / max) + "%";
      box.append(el("div", { class: "bar-row" + (latest ? " latest" : "") },
        el("span", { class: "bar-label", text: fmtDate(snap.date) + (latest ? " (current)" : "") }),
        el("div", { class: "bar-track", role: "img", "aria-label": `${all} pages, ${changed} new or changed` }, segNew, segSame),
        el("span", { class: "bar-num", text: `${all.toLocaleString("en-US")} pages · ${changed.toLocaleString("en-US")} new or changed` })));
    });
  }

  function renderSnapshots(s) {
    renderBars(s);
    $("stats").replaceChildren(
      el("div", { class: "stat" }, el("b", { text: s.chunks.toLocaleString("en-US") }), el("span", { text: "searchable passages" })),
      el("div", { class: "stat" }, el("b", { text: s.facts.toLocaleString("en-US") }), el("span", { text: "extracted facts" })),
      el("div", { class: "stat" }, el("b", { text: String(s.conflicts) }), el("span", { text: "contradictions found" })),
      el("div", { class: "stat" }, el("b", { text: (s.load_seconds || 0) + " s" }), el("span", { text: s.from_cache ? "start (saved copy)" : "start (read all pages)" })));
    $("load-note").textContent = s.from_cache
      ? "This start loaded the saved copy of what the agents learned. It is rebuilt when a snapshot or the code changes."
      : "This start read every page. The result is saved, so the next start is faster.";
  }

  poll();
})();
