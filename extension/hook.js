// hook.js — runs in the MAIN world of every frame on Microsoft origins.
// Patches fetch + XHR so the transcript payload the recap iframe loads is
// mirrored to the extension via window.postMessage. Read-only: never alters
// the request or the response the page sees.
(() => {
  if (window.__hfTcHooked) return;
  window.__hfTcHooked = true;

  const MAX_BODY = 15 * 1024 * 1024; // 15 MB cap
  const INTERESTING = /transcript|streamcontent|vtt|closedcaption/i;

  function report(url, contentType, body) {
    if (!body || body.length > MAX_BODY) return;
    try {
      window.postMessage(
        {
          __hfTc: true,
          kind: "net",
          url: String(url),
          contentType: contentType || "",
          frameUrl: location.href,
          body,
        },
        "*"
      );
    } catch (e) {
      /* never break the page */
    }
  }

  // --- fetch ---
  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args);
    try {
      const url =
        typeof args[0] === "string" ? args[0] : args[0] && args[0].url;
      if (url && INTERESTING.test(url)) {
        p.then((resp) => {
          try {
            const ct = resp.headers.get("content-type") || "";
            resp
              .clone()
              .text()
              .then((t) => report(resp.url || url, ct, t))
              .catch(() => {});
          } catch (e) {}
        }).catch(() => {});
      }
    } catch (e) {}
    return p;
  };

  // --- XHR ---
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__hfTcUrl = url;
    return origOpen.call(this, method, url, ...rest);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    try {
      const url = this.__hfTcUrl;
      if (url && INTERESTING.test(String(url))) {
        this.addEventListener("load", () => {
          try {
            if (this.responseType === "" || this.responseType === "text") {
              report(
                this.responseURL || url,
                this.getResponseHeader("content-type") || "",
                this.responseText
              );
            } else if (this.responseType === "json" && this.response) {
              report(
                this.responseURL || url,
                "application/json",
                JSON.stringify(this.response)
              );
            }
          } catch (e) {}
        });
      }
    } catch (e) {}
    return origSend.apply(this, args);
  };

  // Announce this frame for diagnostics.
  try {
    window.postMessage(
      { __hfTc: true, kind: "hello", frameUrl: location.href },
      "*"
    );
  } catch (e) {}
})();
