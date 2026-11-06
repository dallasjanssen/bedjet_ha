/* BedJet Nights sidebar panel for Home Assistant. Plain JavaScript, no build step.
   Schedules, the night curve and history follow Lull (github.com/rkanjani/lull, MIT). */

const VERSION = new URL(import.meta.url).search;
const STYLESHEET = new URL("app.css", import.meta.url).href.split("?")[0] + VERSION;

function createApp(root, host) {
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const saved = {
    get(key, fallback) {
      try {
        const value = localStorage.getItem("bedjet-nights." + key);
        return value === null ? fallback : JSON.parse(value);
      } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem("bedjet-nights." + key, JSON.stringify(value)); } catch { /* private window */ }
    },
  };

  const S = {
    snap: null,
    offset: 0,
    tab: saved.get("tab", "schedule"),
    unitId: saved.get("unit", null),
    busy: null,
    history: null,
    historyFor: null,
    historyAt: 0,
    expanded: new Set(),
    draft: null,
    draftIsNew: false,
    phaseIndex: null,
    sheet: null,
    dragging: false,
    pendingRender: false,
    remotePending: {},
    toastTimer: null,
    layerKey: "",
  };

  // Modes and limits

  const MODES = {
    cool: { title: "Cool", activity: "Cooling", tint: "var(--cool)", icon: "snow", range: [38, 62], def: 44, max: 719 },
    heat: { title: "Heat", activity: "Heating", tint: "var(--warm)", icon: "flame", range: [45, 80], def: 66, max: 240 },
    extended_heat: { title: "Gentle heat", activity: "Gently heating", tint: "var(--gentle)", icon: "sun", range: [38, 67], def: 58, max: 719 },
    turbo: { title: "Turbo", activity: "Turbo heating", tint: "var(--hot)", icon: "bolt", range: [86, 86], def: 86, max: 10 },
    dry: { title: "Dry", activity: "Drying", tint: "var(--dry)", icon: "wind", range: [40, 80], def: 60, max: 719 },
    wait: { title: "Pause", activity: "Paused", tint: "var(--neutral)", icon: "pause", range: [80, 80], def: 80, max: 719 },
    standby: { title: "Off", activity: "Off", tint: "var(--neutral)", icon: "power", range: [80, 80], def: 80, max: 719 },
  };
  const PHASE_MODES = ["cool", "heat", "extended_heat", "turbo", "dry", "wait"];
  const REMOTE_MODES = ["cool", "heat", "extended_heat", "turbo", "dry", "standby"];
  const V2_MODES = new Set(["standby", "heat", "turbo", "cool", "wait"]);
  const DAYS = ["M", "T", "W", "T", "F", "S", "S"];
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  const usesTemp = (mode) => MODES[mode].range[0] !== MODES[mode].range[1];
  const usesFan = (mode) => mode !== "standby" && mode !== "wait";
  const adjustsTemp = (mode, unit) => (unit && unit.generation === "v2" ? mode === "heat" : usesTemp(mode));

  // Heat hours by fan step (rows) and target from 30 C in half degree pairs, as the BedJet enforces.
  const HEAT_ROWS = (() => {
    const quiet = [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc6, 0x66, 0x66, 0x41];
    const strong = [0xcc, 0xcc, 0xcc, 0xcc, 0x44, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11];
    const middle = [
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc6, 0x66, 0x64, 0x44, 0x21],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc6, 0x64, 0x44, 0x44, 0x21],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc6, 0x64, 0x42, 0x22, 0x21],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc4, 0x44, 0x42, 0x22, 0x21],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc4, 0x44, 0x42, 0x11, 0x11],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xc4, 0x44, 0x44, 0x42, 0x11, 0x11],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xc4, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
      [0xcc, 0xcc, 0xcc, 0xcc, 0xc4, 0x44, 0x44, 0x42, 0x21, 0x11, 0x11],
    ];
    return [...Array(5).fill(quiet), ...middle, ...Array(6).fill(strong)];
  })();

  function longestMinutes(mode, temperature, fanStep) {
    if (mode !== "heat" || temperature < 60) return 719;
    const row = HEAT_ROWS[Math.min(Math.max(fanStep, 0), HEAT_ROWS.length - 1)];
    const offset = temperature - 60;
    const pair = row[Math.min(offset >> 1, row.length - 1)];
    const hours = (offset & 1) === 0 ? pair >> 4 : pair & 0x0f;
    return Math.min(hours * 60, 719);
  }

  const phaseMax = (p) => Math.min(MODES[p.mode].max, longestMinutes(p.mode, p.temperature, p.fan / 5 - 1));
  const totalMinutes = (phases) => phases.reduce((sum, p) => sum + p.minutes, 0);

  function problemFor(schedule, unit) {
    if (!schedule.phases.length) return "Add at least one phase first.";
    if (schedule.phases.some((p) => p.minutes > phaseMax(p))) {
      return "Your BedJet won't run a phase this long. Shorten the Heat phases or use Gentle heat.";
    }
    if (unit && unit.generation === "v2") {
      const missing = [...new Set(schedule.phases.filter((p) => !V2_MODES.has(p.mode)).map((p) => MODES[p.mode].title))];
      if (missing.length) {
        return `${unit.name} is a BedJet V2, which can't run ${missing.join(" or ")}. Edit "${schedule.name}" or pick another schedule for it.`;
      }
    }
    return null;
  }

  // Units and time

  const fahrenheit = () => !S.snap || S.snap.fahrenheit !== false;
  function degFromHalf(half) {
    const c = half / 2;
    return Math.round(fahrenheit() ? (c * 9) / 5 + 32 : c);
  }
  function halfFromDeg(deg) {
    const c = fahrenheit() ? ((deg - 32) * 5) / 9 : deg;
    return Math.round(c * 2);
  }
  function degFromC(c) {
    if (c === null || c === undefined) return null;
    return Math.round(fahrenheit() ? (c * 9) / 5 + 32 : c);
  }

  const tz = () => (S.snap && S.snap.time_zone) || undefined;
  const serverNow = () => Date.now() / 1000 + S.offset;

  function fmt(options, date) {
    try { return new Intl.DateTimeFormat(undefined, options).format(date); } catch { return new Intl.DateTimeFormat(undefined, { ...options, timeZone: undefined }).format(date); }
  }
  function clockFromMinute(minute) {
    const m = ((Math.round(minute) % 1440) + 1440) % 1440;
    return fmt({ hour: "numeric", minute: "2-digit", timeZone: "UTC" }, new Date(Date.UTC(2000, 0, 1, Math.floor(m / 60), m % 60)));
  }
  const clockFromTs = (ts) => fmt({ hour: "numeric", minute: "2-digit", timeZone: tz() }, new Date(ts * 1000));
  function minuteOfDay(ts) {
    try {
      const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz() }).formatToParts(new Date(ts * 1000));
      const get = (type) => Number(parts.find((p) => p.type === type).value);
      return get("hour") * 60 + get("minute");
    } catch {
      const d = new Date(ts * 1000);
      return d.getHours() * 60 + d.getMinutes();
    }
  }
  function duration(minutes) {
    const hours = Math.floor(minutes / 60), rest = Math.round(minutes % 60);
    if (hours === 0) return `${rest} min`;
    return rest === 0 ? `${hours} hr` : `${hours} hr ${rest} min`;
  }
  const minuteToTimeValue = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 10);

  // Icons

  const ICONS = {
    snow: '<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/><path d="m9.5 4.5 2.5 2 2.5-2M9.5 19.5l2.5-2 2.5 2"/>',
    flame: '<path d="M12 3c1 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2.4 1.3-3.8 2.5-5 .3 1.6 1 2.6 2 3 0-3 0-5.5.5-8Z"/>',
    sun: '<circle cx="12" cy="12" r="3.5"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    bolt: '<path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z"/>',
    wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h16a3 3 0 1 1-3 3M3 16h8"/>',
    pause: '<path d="M9 5v14M15 5v14"/>',
    power: '<path d="M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/>',
    sunrise: '<path d="M4 18h16M7 18a5 5 0 0 1 10 0M12 6v3M5.5 10.5 7 12M18.5 10.5 17 12"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    alert: '<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4M12 17h.01"/>',
    play: '<path d="M7 4.5v15l12-7.5-12-7.5Z"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    phone: '<rect x="6.5" y="2.5" width="11" height="19" rx="2"/><path d="M11 18.5h2"/>',
    template: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 14c1.5 0 2-4 4-4s2.5 4 4 4"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  // Night curve

  function level(p) {
    switch (p.mode) {
      case "cool": return -(0.25 + (0.75 * p.fan) / 100);
      case "dry": return -0.15;
      case "standby": case "wait": return 0;
      default: return 0.2 + (0.8 * (p.temperature - 38)) / 48;
    }
  }
  function ramp(a, b) {
    const width = Math.min(40, a.minutes / 2, b.minutes / 2);
    return width > 0 ? width : null;
  }
  function blend(from, to, t) {
    const eased = t * t * (3 - 2 * t);
    return from + (to - from) * eased;
  }
  // Phase levels joined by short eased ramps, so the line reads as a curve rather than steps.
  function levelAt(minute, phases) {
    let start = 0;
    for (let i = 0; i < phases.length; i++) {
      const p = phases[i];
      const end = start + p.minutes;
      if (minute <= end || i === phases.length - 1) {
        if (i > 0) {
          const r = ramp(phases[i - 1], p);
          if (r && minute - start < r) return blend(level(phases[i - 1]), level(p), 0.5 + (minute - start) / (2 * r));
        }
        if (i < phases.length - 1) {
          const r = ramp(p, phases[i + 1]);
          if (r && end - minute < r) return blend(level(p), level(phases[i + 1]), 0.5 - (end - minute) / (2 * r));
        }
        return level(p);
      }
      start = end;
    }
    return 0;
  }

  let curveCount = 0;
  // The night drawn as one line: above the midline is warming, below it is cooling.
  function curve(phases, { height = 110, line = 3, progress = null, selection = null, label = "" } = {}) {
    const id = "c" + ++curveCount;
    const W = 340, inset = 8, width = W - inset * 2, H = height;
    const total = Math.max(totalMinutes(phases), 1);
    const mid = H / 2, amp = H * 0.42;
    const pt = (f) => [inset + width * f, mid - levelAt(f * total, phases) * amp];
    const samples = 170;
    let d = "";
    for (let i = 0; i <= samples; i++) {
      const [x, y] = pt(i / samples);
      d += (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }
    const area = `${d}L${inset + width} ${mid}L${inset} ${mid}Z`;
    let bands = "", ticks = "", elapsed = 0;
    phases.forEach((p, i) => {
      const x = inset + (width * elapsed) / total, w = (width * p.minutes) / total;
      if (i === selection) bands += `<rect x="${x.toFixed(1)}" y="0" width="${w.toFixed(1)}" height="${H}" rx="10" fill="rgba(255,255,255,0.07)"/>`;
      if (elapsed > 0) ticks += `<rect x="${x.toFixed(1)}" y="${mid - 5}" width="1" height="10" fill="rgba(255,255,255,0.18)"/>`;
      elapsed += p.minutes;
    });
    const top = mid - amp, bottom = mid + amp;
    const drawing = `
      <path d="${area}" fill="url(#${id}f)"/>
      <path d="${d}" fill="none" stroke="url(#${id}s)" stroke-width="${line}" stroke-linecap="round" stroke-linejoin="round" filter="url(#${id}g)" opacity="0.6"/>
      <path d="${d}" fill="none" stroke="url(#${id}s)" stroke-width="${line}" stroke-linecap="round" stroke-linejoin="round"/>`;
    let body;
    if (progress === null) {
      body = drawing;
    } else {
      const p = Math.min(Math.max(progress, 0), 1);
      const [nx, ny] = pt(p);
      body = `<g opacity="0.3">${drawing}</g>
        <clipPath id="${id}c"><rect x="0" y="0" width="${(inset + width * p).toFixed(1)}" height="${H}"/></clipPath>
        <g clip-path="url(#${id}c)">${drawing}</g>
        <circle cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="9" fill="rgba(255,255,255,0.7)" filter="url(#${id}h)"/>
        <circle cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="6" fill="#fff"/>`;
    }
    return `<svg class="curve" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label || "Night curve")}">
      <defs>
        <linearGradient id="${id}s" x1="0" y1="${top}" x2="0" y2="${bottom}" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#ff4d2e"/><stop offset="0.3" stop-color="#ff8a3d"/><stop offset="0.5" stop-color="#d9d9d9"/><stop offset="0.7" stop-color="#4da3ff"/><stop offset="1" stop-color="#2b5cff"/>
        </linearGradient>
        <linearGradient id="${id}f" x1="0" y1="${top}" x2="0" y2="${bottom}" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#ff4d2e" stop-opacity="0.45"/><stop offset="0.5" stop-color="#ff8a3d" stop-opacity="0"/><stop offset="0.5" stop-color="#4da3ff" stop-opacity="0"/><stop offset="1" stop-color="#2b5cff" stop-opacity="0.45"/>
        </linearGradient>
        <filter id="${id}g" x="-10%" y="-30%" width="120%" height="160%"><feGaussianBlur stdDeviation="4"/></filter>
        <filter id="${id}h" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="3"/></filter>
      </defs>
      ${bands}${ticks}
      <line x1="${inset}" y1="${mid}" x2="${inset + width}" y2="${mid}" stroke="rgba(255,255,255,0.12)" stroke-dasharray="2 5"/>
      ${body}
    </svg>`;
  }

  function timeline(startMinute, minutes) {
    return `<div class="timeline num">
      <span>${icon("moon")}${esc(clockFromMinute(startMinute))}</span>
      <span>${esc(duration(minutes))}</span>
      <span>${icon("sunrise")}${esc(clockFromMinute(startMinute + minutes))}</span>
    </div>`;
  }

  // Talking to the server

  async function ws(command, payload = {}) {
    let data;
    try {
      data = await host.hass.callWS({ type: `bedjet_nights/${command}`, ...payload });
    } catch (err) {
      throw new Error((err && err.message) || "Something went wrong.");
    }
    if (data && Array.isArray(data.units)) setSnap(data);
    return data;
  }

  async function act(key, work) {
    if (S.busy) return;
    S.busy = key;
    render();
    try { await work(); } catch (err) { toast(err.message); } finally { S.busy = null; render(); }
  }

  function toast(message) {
    clearTimeout(S.toastTimer);
    let el = root.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "alert");
      root.querySelector(".frame").appendChild(el);
    }
    el.textContent = message;
    S.toastTimer = setTimeout(() => el.remove(), 5000);
  }

  function setSnap(snap) {
    S.snap = snap;
    S.offset = snap.now - Date.now() / 1000;
    const ids = snap.units.map((u) => u.id);
    if (!ids.includes(S.unitId)) S.unitId = ids[0] || null;
    render();
  }

  // Live updates: Home Assistant sends the whole state again whenever something changes.
  let unsubscribe = null;
  function connect() {
    if (unsubscribe || !host.hass) return;
    unsubscribe = host.hass.connection
      .subscribeMessage((snap) => setSnap(snap), { type: "bedjet_nights/subscribe" })
      .catch((err) => {
        unsubscribe = null;
        toast((err && err.message) || "BedJet Nights isn't running. Check its integration in Settings.");
      });
  }
  function disconnect() {
    if (!unsubscribe) return;
    unsubscribe.then((stop) => stop && stop()).catch(() => {});
    unsubscribe = null;
  }

  const unit = () => (S.snap ? S.snap.units.find((u) => u.id === S.unitId) : null);
  const schedules = () => (S.snap ? S.snap.schedules : []);
  const scheduleById = (id) => schedules().find((s) => s.id === id);

  // Rendering

  function render() {
    if (S.dragging) { S.pendingRender = true; return; }
    S.pendingRender = false;
    root.querySelectorAll(".nav [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === S.tab)));
    const view = root.getElementById("view");
    let html;
    if (!S.snap) html = header("BedJet Nights") + `<div class="card"><p class="muted">Connecting...</p></div>`;
    else if (S.tab === "remote") html = remoteView();
    else if (S.tab === "history") html = historyView();
    else html = scheduleView();
    view.innerHTML = html;
    if (S.sheet === "device" || S.sheet === "templates") renderLayer();
  }

  function renderLayer() {
    const layer = root.getElementById("layer");
    const overlay = layer.querySelector(".overlay");
    const sheet = layer.querySelector(".sheet");
    const scroll = { overlay: overlay ? overlay.scrollTop : 0, sheet: sheet ? sheet.scrollTop : 0 };
    let html = "";
    if (S.draft) html += editorView();
    if (S.sheet === "phase" && S.draft && S.phaseIndex !== null) html += phaseSheet();
    if (S.sheet === "device") html += deviceSheet();
    if (S.sheet === "templates") html += templatesSheet();
    // Live updates redraw an open sheet; only a sheet that just opened slides in.
    const key = `${S.draft ? "editor" : ""}|${S.sheet}|${S.phaseIndex}`;
    if (key === S.layerKey) html = html.replace(/class="scrim"/g, 'class="scrim steady"');
    S.layerKey = key;
    layer.innerHTML = html;
    const newOverlay = layer.querySelector(".overlay");
    const newSheet = layer.querySelector(".sheet");
    if (newOverlay) newOverlay.scrollTop = scroll.overlay;
    if (newSheet) newSheet.scrollTop = scroll.sheet;
    root.querySelector(".scroller").style.overflowY = html ? "hidden" : "";
  }

  function header(title, eyebrow = "") {
    const u = unit();
    const chip = S.snap && S.snap.units.length
      ? `<button class="unit-chip" data-action="device" aria-label="BedJets and settings, ${esc(u ? u.name : "")}">
           <span class="dot ${u && u.status.available ? "on" : u && u.lend_until ? "warn" : ""}"></span><span>${esc(u ? u.name : "BedJet")}</span>
         </button>`
      : "";
    const menu = host.narrow
      ? `<button class="menu" data-action="menu" aria-label="Show the Home Assistant sidebar">${icon("menu")}</button>`
      : "";
    return `<header class="head">
      <div class="title">${menu}<div><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1></div></div>
      ${chip}
    </header>${banner()}`;
  }

  function banner() {
    if (!S.snap) return "";
    const u = unit();
    if (u && u.lend_until) {
      return `<div class="card tight" style="margin-bottom:16px">
        <p class="warning">${icon("phone")}<span>${esc(u.name)} is lent to a phone until ${esc(clockFromTs(u.lend_until))}. Home Assistant takes it back by itself.</span></p>
        <div style="margin-top:12px"><button class="btn secondary" data-action="reclaim" ${S.busy ? "disabled" : ""}>Take it back now</button></div>
      </div>`;
    }
    return "";
  }

  function dateEyebrow() {
    return fmt({ weekday: "long", month: "short", day: "numeric", timeZone: tz() }, new Date(serverNow() * 1000));
  }

  function noUnits() {
    return `<div class="card stack">
      <h2>No BedJet yet</h2>
      <p class="muted">BedJet Nights works through Home Assistant's BedJet integration. Once your BedJet shows up there, it appears here too.</p>
    </div>`;
  }

  // Schedule tab

  function scheduleView() {
    const u = unit();
    let html = header("Schedule", dateEyebrow());
    if (!u) return html + noUnits();
    html += `<div class="stack">`;
    const tonight = scheduleById(u.tonight);
    if (u.run) html += runningCard(u);
    else if (tonight) html += plannedCard(u, tonight);
    else html += `<div class="card"><h2>No schedules yet</h2><p class="muted">Create one to plan how your night feels.</p></div>`;

    const featured = u.run ? u.run.schedule.id : tonight && tonight.id;
    const others = schedules().filter((s) => s.id !== featured);
    if (others.length) {
      html += `<p class="eyebrow section-label">More schedules</p>`;
      for (const s of others) html += scheduleRow(s, !u.run);
    }
    html += `<button class="btn secondary" data-action="new">${icon("plus")}New Schedule</button>
      <button class="btn secondary" data-action="templates">${icon("template")}Start From a Template</button>`;
    html += `</div>`;
    return html;
  }

  function plannedCard(u, s) {
    const problem = problemFor(s, u);
    const auto = u.auto || { enabled: false, days: [] };
    const start = auto.enabled ? s.bedtime : minuteOfDay(serverNow());
    const starting = S.busy === "start";
    const days = DAYS.map((d, i) => `<button class="${auto.days.includes(i) ? "on" : ""}" data-action="auto-day" data-day="${i}" aria-pressed="${auto.days.includes(i)}" aria-label="${DAY_NAMES[i]}">${d}</button>`).join("");
    return `<section class="card stack" aria-label="Tonight">
      <div class="row" style="align-items:flex-start">
        <div><p class="eyebrow">Tonight</p><h2>${esc(s.name)}</h2></div>
        <button class="link" data-action="edit" data-id="${esc(s.id)}" aria-label="Edit ${esc(s.name)}">Edit</button>
      </div>
      <div>${curve(s.phases, { height: 120, label: `Night curve for ${s.name}` })}${timeline(start, totalMinutes(s.phases))}</div>
      ${problem ? `<p class="warning">${icon("alert")}<span>${esc(problem)}</span></p>` : ""}
      ${u.error ? `<p class="error-line">${esc(u.error)}</p>` : ""}
      <button class="btn primary" data-action="start" ${starting || S.busy || problem || !u.status.available || u.lend_until ? "disabled" : ""}>
        ${starting ? '<span class="spinner" aria-label="Starting"></span>' : "Start Night"}
      </button>
      <p class="faint small center" style="margin:0">Home Assistant plays it phase by phase all night. Your phone can sleep too.</p>
      <div style="border-top:1px solid var(--hairline);padding-top:16px" class="stack">
        <div class="row">
          <div><b style="font-weight:600">Start at bedtime</b><div class="muted small">Starts by itself at ${esc(clockFromMinute(s.bedtime))}</div></div>
          <button class="toggle" role="switch" aria-checked="${auto.enabled}" aria-label="Start at bedtime" data-action="auto"></button>
        </div>
        ${auto.enabled ? `<div class="days" role="group" aria-label="Nights">${days}</div>` : ""}
      </div>
    </section>`;
  }

  function runningCard(u) {
    const run = u.run;
    const s = run.schedule;
    const now = serverNow();
    const total = totalMinutes(s.phases) * 60;
    const progress = Math.min(Math.max((now - run.started_at) / Math.max(total, 1), 0), 1);
    let elapsed = (now - run.started_at) / 60, current = null;
    s.phases.forEach((p, i) => {
      if (current === null && elapsed < p.minutes) current = { p, i, left: Math.max(Math.ceil(p.minutes - elapsed), 1) };
      if (current === null) elapsed -= p.minutes;
    });
    const mode = current ? MODES[current.p.mode] : null;
    return `<section class="card stack" aria-label="Running now">
      <div>
        <p class="eyebrow">${current ? `Phase ${current.i + 1} of ${s.phases.length}` : "Finishing"}</p>
        <h2>${esc(s.name)}</h2>
        ${current ? `<div class="status-line" style="color:${mode.tint}">${icon(mode.icon)}${esc(mode.activity)} · ${esc(duration(current.left))} left</div>` : ""}
      </div>
      <div>${curve(s.phases, { height: 120, progress, label: `Night curve for ${s.name}, ${Math.round(progress * 100)} percent through` })}${timeline(minuteOfDay(run.started_at), totalMinutes(s.phases))}</div>
      ${u.error ? `<p class="error-line">${esc(u.error)}</p>` : ""}
      <button class="btn secondary" data-action="end" ${S.busy ? "disabled" : ""}>${S.busy === "end" ? '<span class="spinner"></span>' : "End Night"}</button>
      <p class="faint small center" style="margin:0">Changes you make meanwhile last until the next phase begins.</p>
    </section>`;
  }

  function scheduleRow(s, canSwap) {
    const phases = s.phases.length;
    return `<div class="schedule-row">
      <button class="open card tight stack" data-action="edit" data-id="${esc(s.id)}" aria-label="${esc(s.name)}, ${esc(duration(totalMinutes(s.phases)))}, ${phases} ${phases === 1 ? "phase" : "phases"}. Edit">
        <h3>${esc(s.name)}</h3>
        ${curve(s.phases, { height: 64, line: 2.5, label: "" })}
        <span class="muted small">${esc(duration(totalMinutes(s.phases)))} · ${phases} ${phases === 1 ? "phase" : "phases"}</span>
      </button>
      ${canSwap ? `<button class="pill use" data-action="use-tonight" data-id="${esc(s.id)}" aria-label="Use ${esc(s.name)} tonight">Use Tonight</button>` : ""}
    </div>`;
  }

  // Remote tab

  function pending(field, actual) {
    const p = S.remotePending[field];
    if (p && p.until > Date.now() && p.value !== actual) return p.value;
    return actual;
  }

  function slider({ id, label, value, min, max, step = 1, text, tint = "#fff", input }) {
    const pct = max > min ? ((value - min) / (max - min)) * 100 : 100;
    return `<div class="slider">
      <div class="value-head"><label for="${id}">${esc(label)}</label><output id="${id}-out" for="${id}">${esc(text)}</output></div>
      <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${value}" data-input="${input}" style="--p:${pct}%;--tint:${tint}">
    </div>`;
  }

  function runtimeLimit(st) {
    const longest = st.runtime_max > 0 ? st.runtime_max : 60;
    const step = longest <= 15 ? 1 : longest <= 240 ? 5 : 15;
    return { min: step, max: Math.max(Math.floor(longest / step) * step, step * 2), step };
  }

  function remoteView() {
    const u = unit();
    let html = header("Remote");
    if (!u) return html + noUnits();
    const st = u.status;
    if (!st.available) {
      return html + `<div class="card"><h2>Can't reach ${esc(u.name)}</h2><p class="muted">Home Assistant isn't connected to it right now. Check that it's plugged in, and that the BedJet app on your phone is closed.</p></div>`;
    }
    const mode = st.mode || "standby";
    const on = mode !== "standby";
    const m = MODES[mode] || MODES.cool;
    const sub = [
      on && st.fan ? `Fan ${st.fan}%` : null,
      st.current !== null && on ? `Air ${degFromC(st.current)}°` : null,
      st.room !== null ? `Room ${degFromC(st.room)}°` : null,
    ].filter(Boolean).join(" · ");
    const big = on && usesTemp(mode) && st.temperature !== null ? `${degFromC(st.temperature)}°` : on ? m.title : "Off";
    html += `<div class="stack">`;
    if (u.run) html += `<div class="card tight"><p class="warning">${icon("moon")}<span>${esc(u.run.schedule.name)} is running. Choosing a mode or a program here ends it.</span></p></div>`;
    html += `<div class="orb-wrap"><div class="orb ${on ? "" : "off"}" style="--tint:${m.tint}" role="status" aria-label="${esc(m.activity)}, ${esc(big)}">
        <p class="eyebrow">${esc(on ? m.activity : "Off")}</p>
        <div class="big">${esc(big)}</div>
        <div class="sub">${esc(sub)}</div>
      </div></div>`;
    html += `<div class="modes" role="group" aria-label="Mode">${REMOTE_MODES.map((k) => {
      const mm = MODES[k];
      const disabled = u.generation === "v2" && !V2_MODES.has(k);
      return `<button class="mode ${k === mode ? "on" : ""}" style="--tint:${mm.tint}" data-action="remote-mode" data-mode="${k}" aria-pressed="${k === mode}" ${disabled ? "disabled" : ""}>${icon(mm.icon)}${esc(mm.title)}</button>`;
    }).join("")}</div>`;

    if (on) {
      const parts = [];
      if (adjustsTemp(mode, u) && st.min_temp !== null && st.max_temp !== null) {
        const lo = degFromC(st.min_temp), hi = Math.max(degFromC(st.max_temp), lo + 1);
        const value = pending("temperature", degFromC(st.temperature));
        parts.push(slider({ id: "r-temp", label: "Temperature", value, min: lo, max: hi, text: `${value}°`, tint: m.tint, input: "remote-temp" }));
      }
      if (usesFan(mode) && st.fan) {
        const value = pending("fan", st.fan);
        parts.push(slider({ id: "r-fan", label: "Fan", value, min: 5, max: 100, step: 5, text: `${value}%`, tint: m.tint, input: "remote-fan" }));
      }
      if (u.has_runtime && st.runtime !== null) {
        const limit = runtimeLimit(st);
        const value = Math.min(Math.max(pending("runtime", st.runtime), limit.min), limit.max);
        parts.push(slider({ id: "r-runtime", label: u.run ? "Phase ends in" : "Turns off in", value, min: limit.min, max: limit.max, step: limit.step, text: duration(value), input: "remote-runtime" }));
      }
      if (parts.length) html += `<div class="card stack" style="gap:22px">${parts.join("")}</div>`;
    }
    if (u.programs.length) {
      html += `<p class="eyebrow section-label">Saved on your BedJet</p>
        <div class="programs">${u.programs.map((p) => `<button class="pill ${st.preset === p ? "on" : ""}" data-action="program" data-name="${esc(p)}">${esc(p)}</button>`).join("")}</div>`;
    }
    html += `</div>`;
    return html;
  }

  // History tab

  function loadHistory(force = false) {
    const u = unit();
    if (!u) return;
    if (!force && S.historyFor === u.id && Date.now() - S.historyAt < 60000) return;
    S.historyFor = u.id;
    S.historyAt = Date.now();
    ws("history", { unit_id: u.id, nights: 30 })
      .then((data) => { S.history = data.nights; render(); })
      .catch((err) => toast(err.message));
  }

  function entryText(e) {
    const d = e.data || {};
    switch (e.kind) {
      case "air": {
        const m = MODES[d.mode] || MODES.cool;
        const bits = [m.title];
        if (usesTemp(d.mode) && d.temperature) bits.push(`${degFromHalf(d.temperature)}°`);
        if (d.fan) bits.push(`Fan ${d.fan}%`);
        return { text: bits.join(" · "), icon: m.icon, tint: m.tint };
      }
      case "off": return { text: d.timer_ran_out ? "Turned off when its timer ran out" : "Turned off", icon: "power", tint: "var(--neutral)" };
      case "schedule_started": {
        const how = d.source === "bedtime" ? " at bedtime" : d.source === "home_assistant" ? " from Home Assistant" : "";
        return { text: `Started ${d.name || "a schedule"}${how}`, icon: "play", tint: "#fff" };
      }
      case "schedule_ended": {
        const name = d.name || "The schedule";
        const text = {
          finished: `${name} finished`,
          by_you: `Ended ${name}`,
          turned_off: `${name} ended when the BedJet was turned off`,
          failed: `${name} couldn't start`,
        }[d.ending] || `${name} ended`;
        return { text, icon: "stop", tint: "#fff" };
      }
      default: return { text: e.kind, icon: "alert", tint: "var(--neutral)" };
    }
  }

  function nightTitle(id) {
    return fmt({ weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }, new Date(id + "T12:00:00Z"));
  }

  function nightSummary(n) {
    const names = n.entries.filter((e) => e.kind === "schedule_started").map((e) => (e.data && e.data.name) || "A schedule");
    if (names.length) return names.join(", ");
    const first = n.entries[0], last = n.entries[n.entries.length - 1];
    if (!first) return "";
    const a = clockFromTs(first.ts), b = clockFromTs(last.ts);
    return a === b ? a : `${a} to ${b}`;
  }

  function historyView() {
    const u = unit();
    let html = header("History");
    if (!u) return html + noUnits();
    loadHistory();
    if (S.history === null || S.historyFor !== u.id) return html + `<div class="card"><p class="muted">Loading...</p></div>`;
    if (!S.history.length) {
      return html + `<div class="card stack"><h2>No nights yet</h2><p class="muted">Every change to your BedJet shows up here with the time and room temperature, so in the morning you can see what you picked overnight.</p></div>`;
    }
    html += `<div class="stack">`;
    for (const n of S.history) {
      const open = S.expanded.has(n.id);
      html += `<section class="card tight">
        <button class="night-head" data-action="night" data-id="${esc(n.id)}" aria-expanded="${open}">
          <div class="titles"><h3>${esc(nightTitle(n.id))}</h3><div class="muted small num">${esc(nightSummary(n))}</div></div>
          <div class="mini">${n.phases.length ? curve(n.phases, { height: 40, line: 2, label: "" }) : ""}</div>
          ${icon("chevron").replace("<svg", '<svg class="chev"')}
        </button>
        ${open ? `<ol class="entries">${n.entries.map((e) => {
          const t = entryText(e);
          const room = e.room !== null && e.room !== undefined ? `<small>Room ${degFromC(e.room)}°</small>` : "";
          return `<li class="entry"><time>${esc(clockFromTs(e.ts))}</time><span class="badge" style="--tint:${t.tint}">${icon(t.icon)}</span><div class="what">${esc(t.text)}${room}</div></li>`;
        }).join("")}</ol>` : ""}
      </section>`;
    }
    html += `</div>`;
    return html;
  }

  // Editor

  function openEditor(schedule, isNew) {
    S.draft = JSON.parse(JSON.stringify(schedule));
    S.draftIsNew = isNew;
    S.phaseIndex = null;
    S.sheet = null;
    renderLayer();
    const input = root.querySelector(".name-input");
    if (isNew && input) input.select();
  }

  function closeEditor() {
    S.draft = null;
    S.sheet = null;
    S.phaseIndex = null;
    renderLayer();
    render();
  }

  function phaseDetail(p, u) {
    const bits = [];
    if (p.mode === "wait") return "Air off";
    if (adjustsTemp(p.mode, u)) bits.push(`${degFromHalf(p.temperature)}°`);
    if (usesFan(p.mode)) bits.push(`Fan ${p.fan}%`);
    return bits.join(" · ");
  }

  function editorView() {
    const d = S.draft;
    const u = unit();
    const total = totalMinutes(d.phases);
    const problem = problemFor(d, u);
    let clock = d.bedtime;
    const rows = d.phases.map((p, i) => {
      const m = MODES[p.mode];
      const starts = clock;
      clock += p.minutes;
      return `<button class="phase-row ${S.phaseIndex === i ? "selected" : ""}" data-action="phase" data-index="${i}" style="--tint:${m.tint}" aria-label="Phase ${i + 1}, ${esc(m.title)}, ${esc(phaseDetail(p, u))}, ${esc(duration(p.minutes))}, starts ${esc(clockFromMinute(starts))}">
        <span class="icon">${icon(m.icon)}</span>
        <span class="main"><b>${esc(m.title)}</b><span>${esc(phaseDetail(p, u))}</span></span>
        <span class="side">${esc(duration(p.minutes))}<span>${esc(clockFromMinute(starts))}</span></span>
        ${icon("chevron").replace("<svg", '<svg class="chev"')}
      </button>`;
    }).join("");
    return `<div class="overlay" role="dialog" aria-modal="true" aria-label="Edit schedule"><div class="app">
      <div class="overlay-bar">
        <button data-action="editor-cancel">Cancel</button>
        <button class="save" data-action="editor-save" ${S.busy ? "disabled" : ""}>Save</button>
      </div>
      <input class="name-input" data-input="name" value="${esc(d.name)}" maxlength="40" aria-label="Schedule name" autocomplete="off">
      <div class="stack">
        <div class="card">${curve(d.phases, { height: 130, selection: S.phaseIndex, label: `Night curve for ${d.name}` })}${timeline(d.bedtime, total)}</div>
        <div class="card tight row">
          <div><b style="font-weight:500;font-size:17px">Usual bedtime</b><div class="muted small">Wake around ${esc(clockFromMinute(d.bedtime + total))}</div></div>
          <input class="time-input" type="time" data-input="bedtime" value="${minuteToTimeValue(d.bedtime)}" aria-label="Usual bedtime">
        </div>
        <p class="eyebrow section-label">Phases</p>
        ${rows}
        <button class="btn secondary" data-action="phase-add" ${d.phases.length >= 32 ? "disabled" : ""}>${icon("plus")}Add Phase</button>
        ${problem ? `<p class="warning">${icon("alert")}<span>${esc(problem)}</span></p>` : ""}
        <button class="btn primary" data-action="editor-tonight" ${S.busy || !u ? "disabled" : ""}>Use Tonight</button>
        ${S.draftIsNew ? "" : `<div class="row" style="justify-content:center;gap:28px;margin-top:6px">
          <button class="link" data-action="editor-duplicate">Duplicate</button>
          <button class="link danger" data-action="editor-delete">Delete Schedule</button>
        </div>`}
      </div>
    </div></div>`;
  }

  function phaseSheet() {
    const p = S.draft.phases[S.phaseIndex];
    const u = unit();
    const m = MODES[p.mode];
    const max = phaseMax(p);
    const parts = [];
    if (adjustsTemp(p.mode, u)) {
      const [low, high] = m.range;
      const lo = degFromHalf(low), hi = Math.max(degFromHalf(high), lo + 1);
      const value = degFromHalf(p.temperature);
      parts.push(slider({ id: "p-temp", label: "Temperature", value, min: lo, max: hi, text: `${value}°`, tint: m.tint, input: "phase-temp" }));
    }
    if (usesFan(p.mode)) parts.push(slider({ id: "p-fan", label: "Fan", value: p.fan, min: 5, max: 100, step: 5, text: `${p.fan}%`, tint: m.tint, input: "phase-fan" }));
    const top = Math.max(Math.floor(max / 5) * 5, 5);
    parts.push(slider({ id: "p-min", label: "Duration", value: Math.min(p.minutes, top), min: 5, max: top, step: 5, text: duration(p.minutes), input: "phase-minutes" }));
    const limited = max < m.max;
    return `<div class="scrim" data-action="sheet-scrim"><div class="sheet" role="dialog" aria-modal="true" aria-label="Phase">
      <div class="sheet-head"><h2>Phase</h2><button data-action="sheet-close">Done</button></div>
      <div class="stack" style="gap:22px">
        <div class="modes" role="group" aria-label="Mode">${PHASE_MODES.map((k) => {
          const mm = MODES[k];
          const disabled = u && u.generation === "v2" && !V2_MODES.has(k);
          return `<button class="mode ${k === p.mode ? "on" : ""}" style="--tint:${mm.tint}" data-action="phase-mode" data-mode="${k}" aria-pressed="${k === p.mode}" ${disabled ? "disabled" : ""}>${icon(mm.icon)}${esc(mm.title)}</button>`;
        }).join("")}</div>
        ${parts.join("")}
        ${limited ? `<p class="faint small" id="heat-note" style="margin:-8px 0 0">At this temperature and fan, BedJet heats for up to ${esc(duration(max))} before its safety cut-off.</p>` : ""}
        <button class="link danger" style="align-self:center;margin-top:4px" data-action="phase-remove">Remove Phase</button>
      </div>
    </div></div>`;
  }

  function templatesSheet() {
    const list = (S.snap ? S.snap.templates : []).map((s) => `<button class="list-item" data-action="use-template" data-id="${esc(s.id)}">
        <span class="grow"><b>${esc(s.name)}</b><small>${esc(duration(totalMinutes(s.phases)))} · ${s.phases.length} phases</small></span>
        <span style="width:96px">${curve(s.phases, { height: 44, line: 2, label: "" })}</span>
      </button>`).join("");
    return `<div class="scrim" data-action="sheet-scrim"><div class="sheet" role="dialog" aria-modal="true" aria-label="Templates">
      <div class="sheet-head"><h2>Templates</h2><button data-action="sheet-close">Done</button></div>
      <div class="stack" style="gap:10px">${list}</div>
    </div></div>`;
  }

  function deviceSheet() {
    const snap = S.snap;
    const u = unit();
    const units = snap.units.map((x) => {
      const status = x.lend_until ? `Lent to a phone until ${clockFromTs(x.lend_until)}` : x.status.available ? "Connected through Home Assistant" : "Not reachable right now";
      return `<button class="list-item ${x.id === S.unitId ? "selected" : ""}" data-action="pick-unit" data-id="${esc(x.id)}" aria-pressed="${x.id === S.unitId}">
        <span class="dot ${x.status.available ? "on" : x.lend_until ? "warn" : ""}"></span>
        <span class="grow"><b>${esc(x.name)}</b><small>${esc(x.model)} · ${esc(status)}</small></span>
      </button>`;
    }).join("");
    let settings = "";
    if (u) {
      const toggles = [];
      if (u.has_led) toggles.push(`<div class="row"><span>LED light</span><button class="toggle" role="switch" aria-checked="${u.status.led === true}" aria-label="LED light" data-action="led" ${u.status.available ? "" : "disabled"}></button></div>`);
      if (u.has_mute) toggles.push(`<div class="row"><span>Mute beeps</span><button class="toggle" role="switch" aria-checked="${u.status.muted === true}" aria-label="Mute beeps" data-action="mute" ${u.status.available ? "" : "disabled"}></button></div>`);
      if (toggles.length) settings += `<div class="card tight stack">${toggles.join("")}</div>`;
      const admin = !host.hass.user || host.hass.user.is_admin;
      const lend = u.lend_until
        ? `<p class="muted small" style="margin:0">Lent until ${esc(clockFromTs(u.lend_until))}. Home Assistant takes it back by itself.</p>
           <button class="btn secondary" data-action="reclaim" ${S.busy ? "disabled" : ""}>Take it back now</button>`
        : `<div class="programs">${[15, 30, 60, 120].map((m) => `<button class="pill" data-action="lend" data-minutes="${m}" ${S.busy || u.run ? "disabled" : ""}>${m < 60 ? `${m} min` : `${m / 60} hr`}</button>`).join("")}</div>
           ${u.run ? `<p class="faint small" style="margin:0">End tonight's schedule first.</p>` : ""}`;
      if (admin) settings += `<div class="card tight stack">
        <div><b style="font-weight:600">Lend to a phone</b>
        <p class="muted small" style="margin:4px 0 0">Your BedJet takes one Bluetooth connection at a time. Lending pauses the BedJet integration in Home Assistant so the BedJet app or Lull can connect, for example to update firmware. Home Assistant takes it back when the time is up.</p></div>
        ${lend}
      </div>`;
    }
    return `<div class="scrim" data-action="sheet-scrim"><div class="sheet" role="dialog" aria-modal="true" aria-label="BedJets">
      <div class="sheet-head"><h2>Your BedJets</h2><button data-action="sheet-close">Done</button></div>
      <div class="stack" style="gap:12px">
        ${units || `<p class="muted">No BedJet found in Home Assistant.</p>`}
        ${settings}
        <p class="faint small center" style="margin:6px 0 0">Start night, End night, Tonight's schedule and Start at bedtime are also on each BedJet's device page, for automations.</p>
      </div>
    </div></div>`;
  }

  // Events

  async function saveDraft() {
    const name = (S.draft.name || "").trim() || "Untitled";
    const data = await ws("schedule/save", { schedule: { ...S.draft, name } });
    return data.schedule;
  }

  const handlers = {
    menu() {
      // Home Assistant opens its sidebar when this event reaches it.
      host.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    },
    device() { S.sheet = "device"; renderLayer(); },
    templates() { S.sheet = "templates"; renderLayer(); },
    "sheet-close"() {
      S.sheet = null;
      S.phaseIndex = null;
      renderLayer();
    },
    "sheet-scrim"(el, event) {
      if (event.target === el) handlers["sheet-close"]();
    },
    "pick-unit"(el) {
      S.unitId = el.dataset.id;
      saved.set("unit", S.unitId);
      S.history = null;
      render();
      renderLayer();
    },
    new() {
      openEditor({ id: newId(), name: "New schedule", bedtime: 22 * 60 + 30, phases: [{ id: newId(), mode: "cool", temperature: 44, fan: 40, minutes: 240 }] }, true);
    },
    "use-template"(el) {
      const t = S.snap.templates.find((s) => s.id === el.dataset.id);
      if (!t) return;
      S.sheet = null;
      openEditor({ ...t, id: newId(), phases: t.phases.map((p) => ({ ...p, id: newId() })) }, true);
    },
    edit(el) {
      const s = scheduleById(el.dataset.id);
      if (s) openEditor(s, false);
    },
    "use-tonight"(el) {
      const u = unit();
      if (u) act("tonight", () => ws("tonight", { unit_id: u.id, schedule_id: el.dataset.id }));
    },
    start() {
      const u = unit();
      if (u) act("start", () => ws("start", { unit_id: u.id }));
    },
    end() {
      const u = unit();
      if (u) act("end", () => ws("end", { unit_id: u.id }));
    },
    auto() {
      const u = unit();
      if (u) act("auto", () => ws("auto", { unit_id: u.id, enabled: !u.auto.enabled, days: u.auto.days }));
    },
    "auto-day"(el) {
      const u = unit();
      if (!u) return;
      const day = Number(el.dataset.day);
      const days = u.auto.days.includes(day) ? u.auto.days.filter((d) => d !== day) : [...u.auto.days, day];
      act("auto", () => ws("auto", { unit_id: u.id, enabled: true, days }));
    },
    "remote-mode"(el) {
      const u = unit();
      if (u) act("mode", () => ws("remote", { unit_id: u.id, mode: el.dataset.mode }));
    },
    program(el) {
      const u = unit();
      if (u) act("program", () => ws("remote", { unit_id: u.id, program: el.dataset.name }));
    },
    led() {
      const u = unit();
      if (u) act("led", () => ws("remote", { unit_id: u.id, led: u.status.led !== true }));
    },
    mute() {
      const u = unit();
      if (u) act("mute", () => ws("remote", { unit_id: u.id, muted: u.status.muted !== true }));
    },
    lend(el) {
      const u = unit();
      if (u) act("lend", () => ws("lend", { unit_id: u.id, minutes: Number(el.dataset.minutes) }));
    },
    reclaim() {
      const u = unit();
      if (u) act("reclaim", () => ws("reclaim", { unit_id: u.id }));
    },
    night(el) {
      const id = el.dataset.id;
      if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id);
      render();
    },
    "editor-cancel"() { closeEditor(); },
    "editor-save"() {
      act("save", async () => {
        await saveDraft();
        closeEditor();
      });
    },
    "editor-tonight"() {
      const u = unit();
      act("save", async () => {
        const schedule = await saveDraft();
        if (u) await ws("tonight", { unit_id: u.id, schedule_id: schedule.id });
        S.tab = "schedule";
        closeEditor();
      });
    },
    "editor-duplicate"() {
      const d = S.draft;
      openEditor({ ...d, id: newId(), name: `${d.name} copy`.slice(0, 40), phases: d.phases.map((p) => ({ ...p, id: newId() })) }, true);
    },
    "editor-delete"() {
      const d = S.draft;
      if (!window.confirm(`Delete "${d.name}"?`)) return;
      act("delete", async () => {
        await ws("schedule/delete", { schedule_id: d.id });
        closeEditor();
      });
    },
    phase(el) {
      S.phaseIndex = Number(el.dataset.index);
      S.sheet = "phase";
      renderLayer();
    },
    "phase-add"() {
      const last = S.draft.phases[S.draft.phases.length - 1];
      const phase = last ? { ...last, id: newId(), minutes: Math.min(60, phaseMax(last)) } : { id: newId(), mode: "cool", temperature: 44, fan: 40, minutes: 60 };
      S.draft.phases.push(phase);
      S.phaseIndex = S.draft.phases.length - 1;
      S.sheet = "phase";
      renderLayer();
    },
    "phase-mode"(el) {
      const p = S.draft.phases[S.phaseIndex];
      const mode = el.dataset.mode;
      const [low, high] = MODES[mode].range;
      p.mode = mode;
      if (p.temperature < low || p.temperature > high || !usesTemp(mode)) p.temperature = MODES[mode].def;
      p.minutes = Math.max(Math.min(p.minutes, Math.floor(phaseMax(p) / 5) * 5), 5);
      renderLayer();
    },
    "phase-remove"() {
      S.draft.phases.splice(S.phaseIndex, 1);
      S.phaseIndex = null;
      S.sheet = null;
      renderLayer();
    },
  };

  root.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) {
      S.tab = tab.dataset.tab;
      saved.set("tab", S.tab);
      if (S.tab === "history") S.historyAt = 0;
      root.querySelector(".scroller").scrollTop = 0;
      render();
      return;
    }
    const el = event.target.closest("[data-action]");
    if (!el || el.disabled) return;
    const handler = handlers[el.dataset.action];
    if (handler) handler(el, event);
  });

  // Sliders and fields

  function updateSliderLook(input, text) {
    const min = Number(input.min), max = Number(input.max), value = Number(input.value);
    input.style.setProperty("--p", `${max > min ? ((value - min) / (max - min)) * 100 : 100}%`);
    const out = root.getElementById(`${input.id}-out`);
    if (out && text !== undefined) out.textContent = text;
  }

  function sendRemote(field, value, body) {
    const u = unit();
    if (!u) return;
    S.remotePending[field] = { value, until: Date.now() + 8000 };
    ws("remote", { unit_id: u.id, ...body }).catch((err) => {
      delete S.remotePending[field];
      toast(err.message);
      render();
    });
  }

  root.addEventListener("input", (event) => {
    const input = event.target;
    const kind = input.dataset && input.dataset.input;
    if (!kind) return;
    const value = Number(input.value);
    const p = S.draft && S.phaseIndex !== null ? S.draft.phases[S.phaseIndex] : null;
    switch (kind) {
      case "name":
        if (S.draft) S.draft.name = input.value;
        break;
      case "phase-temp": {
        const [low, high] = MODES[p.mode].range;
        p.temperature = Math.min(Math.max(halfFromDeg(value), low), high);
        updateSliderLook(input, `${value}°`);
        break;
      }
      case "phase-fan":
        p.fan = value;
        updateSliderLook(input, `${value}%`);
        break;
      case "phase-minutes":
        p.minutes = value;
        updateSliderLook(input, duration(value));
        break;
      case "remote-temp": updateSliderLook(input, `${value}°`); break;
      case "remote-fan": updateSliderLook(input, `${value}%`); break;
      case "remote-runtime": updateSliderLook(input, duration(value)); break;
      default: break;
    }
  });

  root.addEventListener("change", (event) => {
    const input = event.target;
    const kind = input.dataset && input.dataset.input;
    if (!kind) return;
    const value = Number(input.value);
    S.dragging = false;
    switch (kind) {
      case "bedtime": {
        const [h, m] = String(input.value || "22:30").split(":").map(Number);
        if (S.draft && Number.isFinite(h) && Number.isFinite(m)) S.draft.bedtime = h * 60 + m;
        renderLayer();
        break;
      }
      case "phase-temp":
      case "phase-fan": {
        // A hotter or faster Heat phase may be cut off sooner, so the duration follows.
        const p = S.draft.phases[S.phaseIndex];
        p.minutes = Math.max(Math.min(p.minutes, Math.floor(phaseMax(p) / 5) * 5), 5);
        renderLayer();
        break;
      }
      case "phase-minutes": renderLayer(); break;
      case "remote-temp": sendRemote("temperature", value, { temperature: halfFromDeg(value) }); break;
      case "remote-fan": sendRemote("fan", value, { fan: value }); break;
      case "remote-runtime": sendRemote("runtime", value, { runtime: value }); break;
      default: break;
    }
    if (S.pendingRender) render();
  });

  // Live updates must not yank a slider out from under a finger.
  root.addEventListener("pointerdown", (event) => {
    if (event.target.matches && event.target.matches('input[type="range"]')) S.dragging = true;
  });
  const release = () => {
    if (!S.dragging) return;
    setTimeout(() => {
      S.dragging = false;
      if (S.pendingRender) render();
    }, 50);
  };
  root.addEventListener("pointerup", release);
  root.addEventListener("pointercancel", release);

  root.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (S.sheet) handlers["sheet-close"]();
    else if (S.draft) closeEditor();
  });

  // Keep the running card's minutes and the history fresh.
  let timer = null;

  return {
    start() {
      connect();
      if (!timer) {
        timer = setInterval(() => {
          if (S.tab === "history") loadHistory();
          else if (!S.draft) render();
        }, 30000);
      }
      render();
    },
    stop() {
      disconnect();
      clearInterval(timer);
      timer = null;
    },
    render,
  };
}

class BedJetNightsPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._app = null;
    const shadow = this.attachShadow({ mode: "open" });
    shadow.innerHTML = `<link rel="stylesheet" href="${STYLESHEET}">
      <div class="frame">
        <div class="scroller"><main class="app" id="view" aria-live="polite"></main></div>
        <nav class="nav" role="tablist" aria-label="Sections">
          <button role="tab" data-tab="schedule" aria-selected="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/><path d="M17 3v4M15 5h4"/></svg>
            Schedule
          </button>
          <button role="tab" data-tab="remote" aria-selected="false">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="13" r="7"/><path d="M12 13V9.5M9 3h6"/></svg>
            Remote
          </button>
          <button role="tab" data-tab="history" aria-selected="false">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>
            History
          </button>
        </nav>
        <div id="layer"></div>
      </div>`;
  }

  get hass() {
    return this._hass;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._start();
  }

  get narrow() {
    return this._narrow;
  }

  set narrow(narrow) {
    const changed = narrow !== this._narrow;
    this._narrow = narrow;
    if (changed && this._app) this._app.render();
  }

  set panel(_panel) {}

  set route(_route) {}

  connectedCallback() {
    if (this._hass) this._start();
  }

  disconnectedCallback() {
    if (this._app) this._app.stop();
  }

  _start() {
    if (!this._app) this._app = createApp(this.shadowRoot, this);
    this._app.start();
  }
}

if (!customElements.get("bedjet-nights-panel")) customElements.define("bedjet-nights-panel", BedJetNightsPanel);
