// background.js — service worker. Receives captured payloads, converts them
// to VTT + a .meta.json sidecar, and saves both to Downloads/shruta-transcripts/
// where the local Shruta sweeper processes them.

const DIR = "shruta-transcripts";

// ---------- small utils ----------
async function fingerprint(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function b64(s) {
  return btoa(unescape(encodeURIComponent(s)));
}

function cleanTitle(title) {
  // Faithful human title: keeps accents, & and casing (meta.title is what the
  // processor puts in note frontmatter and Notion — never slugify it).
  return (title || "")
    .replace(/\s*[|–—-]\s*Microsoft Teams.*$/i, "")   // drop trailing " | Microsoft Teams"
    .replace(/^\s*(Calendar|Chat)\s*[|–—-]\s*/i, "")  // drop leading "Calendar | " / "Chat | "
    .replace(/\s*[|–—-]?\s*Recap\s*$/i, "")
    .replace(/\s*\|\s*/g, " — ")
    .replace(/\s+/g, " ")
    .trim();
}

function slug(title) {
  return (cleanTitle(title) || "meeting")
    .replace(/[^\p{L}\p{N}]+/gu, "_")   // unicode-aware: Integración stays intact
    .replace(/^_+|_+$/g, "")
    .slice(0, 60) || "meeting";
}

function ts() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    "_" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds())
  );
}

function download(filename, mime, content) {
  return chrome.downloads.download({
    url: `data:${mime};base64,` + b64(content),
    filename: `${DIR}/${filename}`,
    conflictAction: "uniquify",
    saveAs: false,
  });
}

// ---------- transcript payload normalization ----------
const SPEAKER_KEYS = ["speakerDisplayName", "displayName", "speaker", "speakerName", "userDisplayName", "name"];
const TEXT_KEYS = ["text", "spokenText", "content", "displayText", "transcriptText"];
const START_KEYS = ["startOffset", "startTime", "offset", "start", "startDateTime"];

function pick(obj, keys) {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v.trim()) return v.trim();
    if (v && typeof v === "object") {
      const nested = pick(v, SPEAKER_KEYS.concat(TEXT_KEYS));
      if (nested) return nested;
    }
  }
  return "";
}

// Recursively find the largest array of objects that look like speaker turns.
function findTurnsArray(node, depth = 0) {
  if (depth > 6 || node == null) return null;
  if (Array.isArray(node)) {
    const objs = node.filter((x) => x && typeof x === "object");
    if (objs.length >= 3) {
      const good = objs.filter((o) => pick(o, TEXT_KEYS) && pick(o, SPEAKER_KEYS));
      if (good.length >= Math.max(3, objs.length * 0.5)) return objs;
    }
    for (const item of node) {
      const r = findTurnsArray(item, depth + 1);
      if (r) return r;
    }
    return null;
  }
  if (typeof node === "object") {
    let best = null;
    for (const v of Object.values(node)) {
      const r = findTurnsArray(v, depth + 1);
      if (r && (!best || r.length > best.length)) best = r;
    }
    return best;
  }
  return null;
}

function toClock(raw, index) {
  // Accept "HH:MM:SS.fff", ISO durations, seconds, or fall back to a fake
  // sequential time — the processor only needs speaker + text + order.
  if (typeof raw === "string" && /^\d{1,2}:\d{2}:\d{2}/.test(raw)) return raw.slice(0, 8) + ".000";
  const secs = typeof raw === "number" ? raw : parseFloat(raw);
  const s = isFinite(secs) && secs > 0 ? Math.floor(secs) : index * 5;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}.000`;
}

function turnsToVtt(turns) {
  const lines = ["WEBVTT", ""];
  turns.forEach((t, i) => {
    const speaker = pick(t, SPEAKER_KEYS) || "Speaker";
    const text = pick(t, TEXT_KEYS);
    if (!text) return;
    const start = toClock(t[START_KEYS.find((k) => k in t)] , i);
    const end = toClock(turns[i + 1] ? turns[i + 1][START_KEYS.find((k) => k in turns[i + 1])] : null, i + 1);
    lines.push(`${start} --> ${end}`, `<v ${speaker}>${text}</v>`, "");
  });
  return lines.join("\n");
}

// ---------- capture handling ----------
const STATE_KEY = "shrutaState";
const stateStore = chrome.storage.session || chrome.storage.local;
let state = {
  frames: {},
  netSeen: [],
  captures: [],
  hashes: new Set(),
  lastNet: {},
  lastCapture: {},
  titleByTab: {},
};

const stateReady = stateStore.get(STATE_KEY).then((saved) => {
  const value = saved && saved[STATE_KEY];
  if (!value) return;
  state = {
    frames: Object.fromEntries(
      Object.entries(value.frames || {}).map(([tabId, urls]) => [tabId, new Set(urls)])
    ),
    netSeen: value.netSeen || [],
    captures: value.captures || [],
    hashes: new Set(value.hashes || []),
    lastNet: value.lastNet || {},
    lastCapture: value.lastCapture || {},
    titleByTab: value.titleByTab || {},
  };
});

let persistTimer;
function persistState() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    const serializable = {
      ...state,
      frames: Object.fromEntries(
        Object.entries(state.frames).map(([tabId, urls]) => [tabId, Array.from(urls)])
      ),
      hashes: Array.from(state.hashes).slice(-200),
      netSeen: state.netSeen.slice(-50),
      captures: state.captures.slice(-50),
    };
    stateStore.set({ [STATE_KEY]: serializable }).catch(() => {});
  }, 50);
}

function rememberHash(value) {
  state.hashes.add(value);
  if (state.hashes.size > 200) state.hashes.delete(state.hashes.values().next().value);
  persistState();
}

async function saveCapture({
  source,
  url,
  frameUrl,
  pageTitle,
  vtt,
  rawJson,
  txt,
  turnCount,
  scanStats,
  tabId,
}) {
  const base = `${ts()}_${slug(pageTitle)}`;
  const meta = {
    title: cleanTitle(pageTitle) || "meeting",
    pageTitle: pageTitle || "",
    capturedAt: new Date().toISOString(),
    source,
    url: url || "",
    frameUrl: frameUrl || "",
    turnCount: turnCount || 0,
    firstTimestamp: scanStats && scanStats.firstTimestamp || "",
    lastTimestamp: scanStats && scanStats.lastTimestamp || "",
    scanComplete: scanStats ? Boolean(scanStats.complete) : null,
    foundScroller: scanStats ? Boolean(scanStats.foundScroller) : null,
    startedAtTop: scanStats ? Boolean(scanStats.startedAtTop) : null,
    reachedBottom: scanStats ? Boolean(scanStats.reachedBottom) : null,
    scrollRange: scanStats && scanStats.scrollRange || 0,
    scanSteps: scanStats && scanStats.steps || 0,
  };
  // Write the meta sidecar FIRST, transcript LAST. The folder-watcher fires on
  // the last-written file; making a content file last guarantees the meta is
  // already on disk when the sweep runs, so the two never get split (which
  // orphaned the meta in Downloads and broke calendar matching).
  await download(`${base}.meta.json`, "application/json", JSON.stringify(meta, null, 2));
  if (rawJson) await download(`${base}.json`, "application/json", rawJson);
  if (vtt) await download(`${base}.vtt`, "text/vtt", vtt);
  if (txt) await download(`${base}.txt`, "text/plain", txt);
  state.captures.push({ base, ...meta });
  if (tabId != null) state.lastCapture[tabId] = Date.now();
  persistState();

  if (tabId != null) {
    chrome.action.setBadgeText({ text: "✓", tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#1b5e20", tabId });
  }
  const primaryExt = vtt ? "vtt" : txt ? "txt" : "json";
  const msg = `Shruta: transcript captured (${turnCount || "?"} turns, ${source}) → Downloads/${DIR}/${base}.${primaryExt}`;
  if (tabId != null) {
    chrome.tabs.sendMessage(tabId, { type: "toast", text: msg, color: "#1b5e20" }).catch(() => {});
  }
  try {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "Shruta",
      message: msg,
    });
  } catch (e) {}
}

async function handleMessage(msg, sender) {
  const tabId = sender.tab ? sender.tab.id : null;
  if (!msg || !msg.type) return;

  // The top frame reports the real meeting name (a heading). Prefer it over
  // everything — the transcript sub-frame has no title and the tab title is
  // the useless "Calendar | Microsoft Teams".
  if (msg.type === "meeting-title") {
    if (tabId != null && msg.title) {
      state.titleByTab[tabId] = msg.title;
      persistState();
    }
    return;
  }
  const title =
    (tabId != null && state.titleByTab[tabId]) ||
    (msg.pageTitle || "").trim() ||
    (sender.tab && sender.tab.title) ||
    "";

  if (msg.type === "frame-hello") {
    if (tabId != null) {
      (state.frames[tabId] = state.frames[tabId] || new Set()).add(msg.frameUrl);
      persistState();
    }
    return;
  }

  // A frame reports it has started scanning a rendered transcript. Give the
  // user a visible "working" signal: amber badge + an in-page toast so they
  // know it's underway and roughly how long to wait.
  if (msg.type === "scanning") {
    if (tabId != null) {
      chrome.action.setBadgeText({ text: "…", tabId });
      chrome.action.setBadgeBackgroundColor({ color: "#b26a00", tabId });
      chrome.tabs.sendMessage(tabId, {
        type: "toast",
        text: "Shruta: capturing transcript… keep this tab in front; the ✓ appears when it's done (usually 15–60s).",
        color: "#b26a00",
      }).catch(() => {});
    }
    return;
  }

  if (msg.type === "net-capture") {
    state.netSeen.push({ url: msg.url, frameUrl: msg.frameUrl, len: (msg.body || "").length });
    const body = msg.body || "";
    const h = await fingerprint((msg.url || "") + "\0" + body);
    if (state.hashes.has(h)) return;

    if (/^\s*WEBVTT/.test(body)) {
      rememberHash(h);
      if (tabId != null) state.lastNet[tabId] = Date.now();
      const turnCount = (body.match(/-->/g) || []).length;
      await saveCapture({ source: "network-vtt", url: msg.url, frameUrl: msg.frameUrl,
        pageTitle: title, vtt: body, turnCount, tabId });
      return;
    }
    try {
      const parsed = JSON.parse(body);
      const turns = findTurnsArray(parsed);
      if (turns && turns.length >= 3) {
        rememberHash(h);
        if (tabId != null) state.lastNet[tabId] = Date.now();
        await saveCapture({ source: "network-json", url: msg.url, frameUrl: msg.frameUrl,
          pageTitle: title, vtt: turnsToVtt(turns), rawJson: body,
          turnCount: turns.length, tabId });
      }
    } catch (e) { /* not JSON, not VTT — ignore */ }
    return;
  }

  if (msg.type === "dom-result") {
    if (!msg.turns || msg.turns.length === 0) return;
    if (msg.scanStats && msg.scanStats.complete === false) {
      if (tabId != null) {
        chrome.action.setBadgeText({ text: "!", tabId });
        chrome.action.setBadgeBackgroundColor({ color: "#b3261e", tabId });
        chrome.tabs.sendMessage(tabId, {
          type: "toast",
          text: "Shruta: capture was incomplete and was not saved. Keep the Transcript tab open, then click Shruta to retry.",
          color: "#b3261e",
        }).catch(() => {});
      }
      return;
    }
    // If the network hook already captured this tab, the DOM scan is a
    // lower-quality duplicate — drop it.
    if (tabId != null && state.lastNet[tabId] &&
        Date.now() - state.lastNet[tabId] < 5 * 60 * 1000) return;
    // Per-tab cooldown: once we've captured this tab, ignore further DOM
    // results for 3 min. Prevents the double-.txt when two frames/scans fire.
    if (tabId != null && state.lastCapture[tabId] &&
        Date.now() - state.lastCapture[tabId] < 3 * 60 * 1000) return;
    const content = msg.turns.join("\n");
    const h = await fingerprint("dom\0" + content);
    if (state.hashes.has(h)) return;
    rememberHash(h);
    // DOM text isn't VTT/JSON — save as .txt (processor normalizes it).
    await saveCapture({ source: "dom-" + msg.method + (msg.auto ? "-auto" : ""),
      frameUrl: msg.frameUrl,
      pageTitle: title,
      txt: content,
      turnCount: msg.turns.length,
      scanStats: msg.scanStats,
      tabId,
    });
    return;
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  stateReady
    .then(() => handleMessage(msg, sender))
    .then(() => sendResponse({ ok: true }))
    .catch((error) => {
      console.warn("Shruta message failed", error);
      sendResponse({ ok: false });
    });
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  stateReady.then(() => {
    delete state.frames[tabId];
    delete state.lastNet[tabId];
    delete state.lastCapture[tabId];
    delete state.titleByTab[tabId];
    persistState();
  });
});

// Toolbar click: force a DOM scan. Diagnostics remain in extension state and
// are not downloaded during normal use, avoiding an unnecessary save prompt.
chrome.action.onClicked.addListener(async (tab) => {
  await stateReady;
  chrome.action.setBadgeText({ text: "…", tabId: tab.id });
  chrome.action.setBadgeBackgroundColor({ color: "#b26a00", tabId: tab.id });
  chrome.tabs.sendMessage(tab.id, {
    type: "toast",
    text: "Shruta: scanning this page for a transcript… keep it in front.",
    color: "#b26a00",
  }).catch(() => {});
  chrome.tabs.sendMessage(tab.id, { type: "dom-scan" }).catch(() => {});
});
