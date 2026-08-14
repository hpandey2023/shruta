const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "capture.js"), "utf8");
const timestampSelector = '[id*="timestamp" i], time, span, div';
const explicitScrollerSelector =
  '#scrollToTargetTargetedFocusZone, ' +
  '[data-testid="scroll-to-target-targeted-focus-zone"], ' +
  '[data-tid="scroll-to-target-targeted-focus-zone"]';

function element(overrides = {}) {
  return {
    children: [],
    childElementCount: 0,
    clientHeight: 0,
    innerText: "",
    parentElement: null,
    scrollHeight: 0,
    scrollTop: 0,
    textContent: "",
    ...overrides,
  };
}

function loadCapture({
  topFrame = true,
  transcriptFrame = true,
  explicitScroller = null,
  timestamps = [],
} = {}) {
  const runtimeListeners = [];
  const runtimeMessages = [];
  const intervalCallbacks = [];
  const timeoutCallbacks = [];
  const document = {
    title: "Calendar | Microsoft Teams",
    body: element(),
    documentElement: element(),
    querySelector(selector) {
      if (selector === explicitScrollerSelector) return explicitScroller;
      if (selector.includes("#OneTranscript")) return transcriptFrame ? element() : null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === timestampSelector) return timestamps;
      return [];
    },
    getElementById() {
      return null;
    },
    createElement() {
      return element({ style: {}, setAttribute() {} });
    },
  };
  const window = {
    addEventListener() {},
    postMessage() {},
  };
  window.top = topFrame ? window : {};
  const chrome = {
    runtime: {
      sendMessage(message) {
        runtimeMessages.push(message);
        return Promise.resolve();
      },
      onMessage: {
        addListener(listener) {
          runtimeListeners.push(listener);
        },
      },
    },
  };
  const context = vm.createContext({
    Array,
    Boolean,
    Map,
    Math,
    Number,
    Promise,
    RegExp,
    Set,
    String,
    chrome,
    clearInterval() {},
    clearTimeout() {},
    document,
    getComputedStyle(node) {
      return { overflowY: node.overflowY || "visible" };
    },
    location: {
      href: transcriptFrame
        ? "https://example.sharepoint.com/_layouts/15/xplatplugins.aspx"
        : "https://teams.cloud.microsoft/",
      pathname: transcriptFrame ? "/_layouts/15/xplatplugins.aspx" : "/",
    },
    setInterval(callback) {
      intervalCallbacks.push(callback);
      return intervalCallbacks.length;
    },
    setTimeout(callback) {
      timeoutCallbacks.push(callback);
      return timeoutCallbacks.length;
    },
    window,
  });
  vm.runInContext(source, context, { filename: "capture.js" });
  return { context, intervalCallbacks, runtimeListeners, runtimeMessages, timeoutCallbacks };
}

test("selects the current Teams transcript scroller directly", () => {
  const scroller = element({
    clientHeight: 338,
    scrollHeight: 26115,
    overflowY: "auto",
  });
  const { context } = loadCapture({ explicitScroller: scroller });
  assert.equal(context.findTranscriptScroller(), scroller);
});

test("finds a transcript scroller more than ten ancestors above a timestamp", () => {
  const timestamp = element({ textContent: "0:08" });
  let current = timestamp;
  for (let depth = 0; depth < 10; depth += 1) {
    const parent = element();
    current.parentElement = parent;
    current = parent;
  }
  const scroller = element({
    clientHeight: 338,
    scrollHeight: 26115,
    overflowY: "auto",
  });
  current.parentElement = scroller;
  const { context } = loadCapture({ timestamps: [timestamp] });
  assert.equal(context.findTranscriptScroller(), scroller);
});

test("auto-starts when timestamps render even if the first DOM probe is one block", () => {
  const scroller = element({
    clientHeight: 338,
    scrollHeight: 26115,
    overflowY: "auto",
  });
  const timestamps = ["0:08", "0:13", "0:16"].map((value) =>
    element({ textContent: value })
  );
  const result = loadCapture({
    topFrame: false,
    explicitScroller: scroller,
    timestamps,
  });
  assert.equal(result.intervalCallbacks.length, 1);
  result.intervalCallbacks[0]();
  assert.ok(result.runtimeMessages.some((message) => message.type === "scanning"));
  assert.equal(result.timeoutCallbacks.length, 1);
});

test("manual scans ignore the Teams shell instead of chat timestamps", () => {
  const result = loadCapture({ topFrame: true, transcriptFrame: false });
  const listener = result.runtimeListeners.at(-1);
  let response;
  listener({ type: "dom-scan" }, {}, (value) => {
    response = value;
  });
  assert.equal(response.ack, false);
  assert.equal(response.reason, "not-transcript-frame");
});
