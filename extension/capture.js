// capture.js — ISOLATED-world content script in every frame on Microsoft
// origins. Relays MAIN-world hook messages to the background, performs the
// DOM-scan fallback on demand, and (top frame only) shows the capture toast.

// ---------- relay MAIN-world messages ----------
window.addEventListener("message", (ev) => {
  const d = ev.data;
  if (!d || !d.__hfTc || ev.source !== window) return;
  try {
    if (d.kind === "net") {
      chrome.runtime.sendMessage({
        type: "net-capture",
        url: d.url,
        contentType: d.contentType,
        frameUrl: d.frameUrl,
        body: d.body,
        pageTitle: document.title,
      });
    } else if (d.kind === "hello") {
      chrome.runtime.sendMessage({ type: "frame-hello", frameUrl: d.frameUrl });
    }
  } catch (e) {}
});

// Also announce frames where the MAIN hook could not run.
try {
  chrome.runtime.sendMessage({ type: "frame-hello", frameUrl: location.href });
} catch (e) {}

// ---------- meeting title (top frame only) ----------
// The recap tab's document.title is "Calendar | Microsoft Teams" — useless as a
// filename. The real meeting name is a heading on the page. Find it and report
// it to the background so captures (which come from the title-less transcript
// sub-frame) are named after the meeting, not "Calendar".
if (window === window.top) {
  const BAD = /^(Chat|Calendar|Recap|Q&A|Shared files?|Notes|AI summary|Transcript|Activity|Copilot|Chats|Favorites|Drafts|Search|Join|Meeting Whiteboard|Oops|Lexia|Teams|Calls|OneDrive|Upgrade.*|Microsoft 365.*)$/i;
  function cleanTabTitle() {
    // Teams tab titles look like "Calendar | <meeting name> | Microsoft Teams"
    // or "Chat | <meeting name> | Microsoft Teams" — the middle is the name.
    // Sometimes there's no middle segment ("Calendar | Microsoft Teams").
    const parts = (document.title || "")
      .split("|")
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((s) => !/^Microsoft Teams$/i.test(s) && !/^(Calendar|Chat)$/i.test(s));
    const name = parts.join(" | ").trim();
    return name && !BAD.test(name) ? name : "";
  }
  function meetingTitle() {
    // Prefer the tab title's middle segment (reliable when present)…
    const fromTab = cleanTabTitle();
    if (fromTab) return fromTab;
    // …otherwise fall back to a recap heading.
    const cands = Array.from(document.querySelectorAll('[role="heading"], h1, h2'))
      .map((h) => (h.textContent || "").trim())
      .filter((t) => t && t.length >= 3 && t.length <= 120 && !BAD.test(t));
    return cands[0] || "";
  }
  let lastSent = "";
  let tries = 0;
  const titleTimer = setInterval(() => {
    tries++;
    const t = meetingTitle();
    if (t && t !== lastSent) {
      lastSent = t;
      try { chrome.runtime.sendMessage({ type: "meeting-title", title: t }); } catch (e) {}
    }
    if (tries > 30) clearInterval(titleTimer); // ~60s of polling is plenty
  }, 2000);
}

// ---------- DOM-scan fallback ----------
// Heuristic scrape of a rendered transcript list. Primary capture is the
// network hook; this exists so a supervised run can still pull *something*
// (plus diagnostics) if the payload route fails.
const TIME_RE = /^\d{1,2}:\d{2}(:\d{2})?$/;

function scanTranscriptDom() {
  // Strategy 1: explicit transcript markers Teams tends to use.
  const marked = document.querySelectorAll(
    '[data-tid*="transcript" i], [class*="transcript" i] li, [class*="entry" i][class*="transcript" i]'
  );
  const turns = [];
  const seen = new Set();

  const harvest = (root) => {
    const text = (root.innerText || "").trim();
    if (!text || text.length < 2) return;
    if (seen.has(text)) return;
    seen.add(text);
    turns.push(text);
  };

  if (marked.length > 3) {
    marked.forEach(harvest);
    return { method: "marked", turns };
  }

  // Strategy 2: look for repeated blocks containing a m:ss timestamp element.
  const timeEls = Array.from(document.querySelectorAll("span,div")).filter(
    (el) => el.childElementCount === 0 && TIME_RE.test((el.textContent || "").trim())
  );
  if (timeEls.length > 3) {
    timeEls.forEach((el) => {
      // climb to the row container: the nearest ancestor with a name + text
      let row = el;
      for (let i = 0; i < 4 && row.parentElement; i++) row = row.parentElement;
      harvest(row);
    });
    return { method: "timestamp-rows", turns };
  }

  // Strategy 3 (last ditch): whole-frame text if it smells like a transcript.
  const body = (document.body && document.body.innerText) || "";
  if (body.length > 500 && timeEls.length > 0) {
    return { method: "innertext", turns: [body] };
  }
  return { method: "none", turns: [] };
}

async function autoScrollAndScan() {
  // Score scrollable ancestors by how many visible timestamp elements they
  // contain. The tallest element is often the whole recap page, not the
  // virtualized transcript list.
  const scores = new Map();
  const visibleTimes = Array.from(document.querySelectorAll("span,div")).filter(
    (el) => el.childElementCount === 0 && TIME_RE.test((el.textContent || "").trim())
  );
  for (const timeEl of visibleTimes) {
    let ancestor = timeEl.parentElement;
    for (let depth = 0; ancestor && depth < 10; depth++, ancestor = ancestor.parentElement) {
      if (ancestor.scrollHeight > ancestor.clientHeight + 100 && ancestor.clientHeight > 120) {
        scores.set(ancestor, (scores.get(ancestor) || 0) + 1);
      }
    }
  }
  const sc = Array.from(scores.entries())
    .sort((a, b) => b[1] - a[1] || b[0].scrollHeight - a[0].scrollHeight)[0]?.[0] || null;
  const all = [];
  const seen = new Set();
  const collect = () => {
    const r = scanTranscriptDom();
    for (const t of r.turns) {
      if (!seen.has(t)) {
        seen.add(t);
        all.push(t);
      }
    }
    return r.method;
  };
  let method = collect();
  let steps = 0;
  let reachedBottom = !sc;
  if (sc) {
    sc.scrollTop = 0;
    await new Promise((r) => setTimeout(r, 500));
    method = collect();
    let bottomWithoutGrowth = 0;
    // The bound is a safety valve. At 450ms per step it allows long meetings
    // while normal 30–60 minute transcripts finish in under a minute.
    for (let i = 0; i < 600; i++) {
      steps++;
      const before = seen.size;
      const maxScroll = Math.max(0, sc.scrollHeight - sc.clientHeight);
      sc.scrollTop = Math.min(maxScroll, sc.scrollTop + Math.floor(sc.clientHeight * 0.85));
      await new Promise((r) => setTimeout(r, 450));
      collect();
      const atBottom = sc.scrollTop >= Math.max(0, sc.scrollHeight - sc.clientHeight - 2);
      bottomWithoutGrowth = atBottom && seen.size === before ? bottomWithoutGrowth + 1 : 0;
      if (bottomWithoutGrowth >= 3) {
        reachedBottom = true;
        break;
      }
    }
    // Force a final bottom sample in case the list expanded during the scan.
    sc.scrollTop = sc.scrollHeight;
    await new Promise((r) => setTimeout(r, 500));
    collect();
    reachedBottom = reachedBottom || sc.scrollTop >= sc.scrollHeight - sc.clientHeight - 2;
  }
  const clocks = all
    .flatMap((turn) => (turn.match(/\b\d{1,2}:\d{2}(?::\d{2})?\b/g) || []))
    .map((clock) => {
      const parts = clock.split(":").map(Number);
      return {
        clock,
        seconds: parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1],
      };
    })
    .sort((a, b) => a.seconds - b.seconds);
  return {
    method,
    turns: all,
    frameUrl: location.href,
    pageTitle: document.title,
    scanStats: {
      firstTimestamp: clocks[0]?.clock || "",
      lastTimestamp: clocks[clocks.length - 1]?.clock || "",
      reachedBottom,
      steps,
    },
  };
}

// ---------- automatic scan in transcript sub-frames ----------
// The transcript renders in a cross-origin sub-frame (SharePoint xplatplugins
// on this tenant). If the network hook doesn't fire, auto-run the DOM scan as
// soon as transcript rows render — no toolbar click needed. Sub-frames only:
// the top Teams frame is full of chat timestamps that would false-positive.
if (window !== window.top) {
  let autoScanStarted = false;
  let checks = 0;
  const probeTimer = setInterval(() => {
    checks++;
    if (autoScanStarted || checks > 40) {
      clearInterval(probeTimer);
      return;
    }
    const probe = scanTranscriptDom();
    if (probe.turns.length >= 3) {
      autoScanStarted = true;
      clearInterval(probeTimer);
      // Tell the user we're on it (amber badge + toast via background).
      try { chrome.runtime.sendMessage({ type: "scanning" }); } catch (e) {}
      // Let the virtualized list settle, then scan. The background suppresses
      // this result if the network hook already captured this tab.
      setTimeout(() => {
        autoScrollAndScan().then((result) => {
          try {
            chrome.runtime.sendMessage({ type: "dom-result", auto: true, ...result });
          } catch (e) {}
        });
      }, 5000);
    }
  }, 3000);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "dom-scan") {
    autoScrollAndScan().then((result) => {
      try {
        chrome.runtime.sendMessage({ type: "dom-result", ...result });
      } catch (e) {}
    });
    sendResponse({ ack: true, frameUrl: location.href });
    return false;
  }
  if (msg && msg.type === "toast" && window === window.top) {
    showToast(msg.text, msg.color);
    sendResponse({ ack: true });
    return false;
  }
  return false;
});

// ---------- toast (top frame only) ----------
function showToast(text, color) {
  try {
    let el = document.getElementById("hf-tc-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "hf-tc-toast";
      el.setAttribute("role", "status");
      el.style.cssText =
        "position:fixed;bottom:16px;right:16px;z-index:2147483647;" +
        "color:#fff;padding:10px 14px;border-radius:8px;" +
        "font:13px/1.4 -apple-system,Segoe UI,sans-serif;max-width:360px;" +
        "box-shadow:0 4px 14px rgba(0,0,0,.35)";
      (document.body || document.documentElement).appendChild(el);
    }
    el.style.background = color || "#1b5e20";
    el.textContent = text;
    el.style.display = "block";
    clearTimeout(el.__hfTimer);
    // "capturing…" (amber) can sit until replaced; success (green) auto-hides.
    const hideAfter = (color && color !== "#1b5e20") ? 600000 : 120000;
    el.__hfTimer = setTimeout(() => (el.style.display = "none"), hideAfter);
  } catch (e) {}
}
