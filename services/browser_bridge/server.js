/**
 * Grok Browser Bridge Server
 *
 * Proxies grok.com app-chat requests through a real Chromium browser
 * to bypass Cloudflare Bot Management.
 *
 * Usage:
 *   BRIDGE_PORT=3080 CHROMIUM_PATH=/usr/bin/chromium node server.js
 *
 * API:
 *   POST /api/chat  { sso, payload }  → streaming grok response body
 *   GET  /health                      → { status: "ok", pages: N }
 */

"use strict";

const http = require("http");
const { chromium } = require("playwright");

const PORT = parseInt(process.env.BRIDGE_PORT || "3080", 10);
const CHROMIUM_PATH = process.env.CHROMIUM_PATH || "/usr/bin/chromium";
const MAX_PAGES = parseInt(process.env.BRIDGE_MAX_PAGES || "10", 10);
const PAGE_IDLE_MS = parseInt(process.env.BRIDGE_PAGE_IDLE_MS || "300000", 10); // 5 min
const NAV_TIMEOUT = 45000;
const REQ_TIMEOUT = 120000;
const PAGE_READY_TIMEOUT = 15000;

const ERROR_STATUS = {
  invalid_json: 400,
  invalid_request: 400,
  bridge_unavailable: 503,
  navigation_timeout: 504,
  sso_unavailable: 401,
  page_not_prepared: 503,
  page_busy: 429,
  request_timeout: 504,
};

let lastBridgeErrorCode = null;
let lastBridgeError = null;
let lastBridgeErrorAt = null;

/** @type {import('playwright').Browser | null} */
let browser = null;

/** @typedef {{ page: import('playwright').Page, context: import('playwright').BrowserContext, sso: string, ready: boolean, preparing: boolean, busy: boolean, lastUsed: number, queue: Array<{payload: object, resolve: Function, reject: Function}>, last_error_code: string | null, last_error: string | null, last_error_at: string | null, last_ready_at: string | null, user_authenticated: boolean }} PageSlot */

/** @type {Map<string, PageSlot>} */
const pages = new Map();

// ---------------------------------------------------------------------------
// Browser lifecycle
// ---------------------------------------------------------------------------

async function ensureBrowser() {
  if (browser && browser.isConnected()) return browser;
  log("Launching browser...");
  try {
    browser = await chromium.launch({
      executablePath: CHROMIUM_PATH,
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
      ],
    });
  } catch (err) {
    const message = `Failed to launch browser: ${err.message}`;
    log(message);
    setBridgeError("bridge_unavailable", message);
    throw new BridgeError("bridge_unavailable", message);
  }
  browser.on("disconnected", () => {
    log("Browser disconnected, clearing pages");
    pages.clear();
    browser = null;
  });
  log("Browser launched");
  return browser;
}

// ---------------------------------------------------------------------------
// Page pool
// ---------------------------------------------------------------------------

async function getOrCreatePage(sso) {
  let slot = pages.get(sso);
  if (slot) {
    if (slot.ready) {
      slot.lastUsed = Date.now();
      return slot;
    }
    if (slot.preparing) {
      throw new BridgeError("page_not_prepared", "Bridge page is still preparing");
    }
    await destroyPage(sso);
  }

  // Evict oldest idle page if at capacity
  if (pages.size >= MAX_PAGES) {
    let oldest = null;
    let oldestTime = Infinity;
    for (const [key, s] of pages) {
      if (!s.busy && s.lastUsed < oldestTime) {
        oldest = key;
        oldestTime = s.lastUsed;
      }
    }
    if (oldest) {
      log(`Evicting idle page for SSO ...${oldest.slice(-8)}`);
      await destroyPage(oldest);
    }
  }

  const b = await ensureBrowser();

  const context = await b.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1080 },
  });

  await context.addCookies([
    { name: "sso", value: sso, domain: ".grok.com", path: "/" },
  ]);

  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

  const page = await context.newPage();

  slot = {
    page,
    context,
    sso,
    ready: false,
    busy: false,
    lastUsed: Date.now(),
    queue: [],
    preparing: true,
    last_error_code: null,
    last_error: null,
    last_error_at: null,
    last_ready_at: null,
    user_authenticated: false,
  };
  pages.set(sso, slot);

  // Set up the route interceptor
  await page.route("**/rest/app-chat/conversations/new", async (route, req) => {
    const s = pages.get(sso);
    if (!s || s.queue.length === 0) {
      // No pending request – let the browser's own request proceed normally
      await route.continue();
      return;
    }

    const pending = s.queue.shift();
    try {
      const originalBody = JSON.parse(req.postData());
      const merged = { ...originalBody, ...pending.payload };

      const response = await route.fetch({
        postData: JSON.stringify(merged),
      });
      const body = await response.text();
      pending.resolve({ status: response.status(), body });
      await route.fulfill({ response, body });
    } catch (err) {
      pending.reject(err);
      try { await route.abort(); } catch (_) { /* ignore */ }
    }
  });

  try {
    await preparePage(slot);
    log(`Page ready for SSO ...${sso.slice(-8)}`);
    return slot;
  } catch (err) {
    const bridgeErr = err instanceof BridgeError
      ? err
      : new BridgeError("page_not_prepared", err.message || "Page preparation failed");
    setSlotError(slot, bridgeErr.code, bridgeErr.message);
    log(`Page preparation failed for SSO ...${sso.slice(-8)}: ${bridgeErr.message}`);
    await destroyPage(sso);
    throw bridgeErr;
  }
}

async function preparePage(slot) {
  const { context, page, sso } = slot;

  // Navigate only to DOM readiness; grok.com keeps long-lived requests open.
  log(`Navigating to grok.com for SSO ...${sso.slice(-8)}`);
  try {
    await page.goto("https://grok.com/", {
      waitUntil: "domcontentloaded",
      timeout: NAV_TIMEOUT,
    });
  } catch (err) {
    throw new BridgeError(
      "navigation_timeout",
      `Navigation to grok.com timed out or failed: ${err.message}`
    );
  }

  try {
    await page.locator("textarea, [contenteditable]").first().waitFor({
      state: "visible",
      timeout: PAGE_READY_TIMEOUT,
    });
  } catch (err) {
    throw new BridgeError(
      "page_not_prepared",
      `Grok composer was not ready: ${err.message}`
    );
  }

  const cookies = await context.cookies("https://grok.com");
  const hasUserId = cookies.some((c) => c.name === "x-userid");
  if (!hasUserId) {
    throw new BridgeError(
      "sso_unavailable",
      "SSO cookie did not authenticate with grok.com"
    );
  }

  slot.ready = true;
  slot.preparing = false;
  slot.user_authenticated = true;
  slot.last_ready_at = new Date().toISOString();
  clearSlotError(slot);
  clearBridgeError();
  return slot;
}

async function destroyPage(sso) {
  const slot = pages.get(sso);
  if (!slot) return;
  pages.delete(sso);
  try { await slot.context.close(); } catch (_) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Send a message through the browser bridge
// ---------------------------------------------------------------------------

async function sendMessage(sso, payload) {
  const slot = await getOrCreatePage(sso);
  if (!slot.ready) {
    throw new BridgeError("page_not_prepared", "Bridge page is not prepared");
  }
  if (slot.preparing) {
    throw new BridgeError("page_not_prepared", "Bridge page is still preparing");
  }
  if (!slot.user_authenticated) {
    throw new BridgeError("sso_unavailable", "SSO is not authenticated with grok.com");
  }
  if (slot.busy) {
    throw new BridgeError("page_busy", "Bridge page busy, retry later");
  }

  slot.busy = true;
  slot.lastUsed = Date.now();

  try {
    const result = await new Promise(async (resolve, reject) => {
      let timeout = null;
      const rejectRequest = (e) => {
        if (timeout) clearTimeout(timeout);
        reject(e);
      };

      timeout = setTimeout(() => {
        const idx = slot.queue.findIndex((q) => q.reject === rejectRequest);
        if (idx >= 0) slot.queue.splice(idx, 1);
        reject(new BridgeError("request_timeout", "Bridge request timeout"));
      }, REQ_TIMEOUT);

      slot.queue.push({
        payload,
        resolve: (r) => {
          if (timeout) clearTimeout(timeout);
          resolve(r);
        },
        reject: rejectRequest,
      });

      try {
        await triggerSend(slot);
      } catch (err) {
        if (timeout) clearTimeout(timeout);
        const idx = slot.queue.findIndex((q) => q.reject === rejectRequest);
        if (idx >= 0) slot.queue.splice(idx, 1);
        reject(err);
      }
    });

    clearSlotError(slot);
    clearBridgeError();
    return result;
  } finally {
    slot.busy = false;
  }
}

async function triggerSend(slot) {
  const { page } = slot;

  try {
    await page.goto("https://grok.com/", {
      waitUntil: "domcontentloaded",
      timeout: NAV_TIMEOUT,
    });
  } catch (err) {
    throw new BridgeError(
      "navigation_timeout",
      `Re-navigation to grok.com timed out or failed: ${err.message}`
    );
  }

  const inputLocator = page.locator("textarea, [contenteditable]").first();
  try {
    await inputLocator.waitFor({ state: "visible", timeout: PAGE_READY_TIMEOUT });
  } catch (err) {
    throw new BridgeError("page_not_prepared", `Grok composer was not ready: ${err.message}`);
  }
  await inputLocator.click();
  await page.keyboard.type("x", { delay: 30 });
  await page.waitForTimeout(300);

  const sendBtn = page.locator(
    'button[aria-label*="end"], button[aria-label*="End"], button[type="submit"]'
  ).first();
  try {
    await sendBtn.click({ timeout: 3000 });
  } catch (_) {
    await page.keyboard.press("Enter");
  }
}

// ---------------------------------------------------------------------------
// Idle page reaper
// ---------------------------------------------------------------------------

setInterval(async () => {
  const now = Date.now();
  for (const [sso, slot] of pages) {
    if (!slot.busy && now - slot.lastUsed > PAGE_IDLE_MS) {
      log(`Reaping idle page for SSO ...${sso.slice(-8)}`);
      await destroyPage(sso);
    }
  }
}, 60000);

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------

function log(msg) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] [bridge] ${msg}`);
}

class BridgeError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
    this.status = status || ERROR_STATUS[code] || 502;
  }
}

function setBridgeError(code, message) {
  lastBridgeErrorCode = code;
  lastBridgeError = message;
  lastBridgeErrorAt = new Date().toISOString();
}

function clearBridgeError() {
  lastBridgeErrorCode = null;
  lastBridgeError = null;
  lastBridgeErrorAt = null;
}

function setSlotError(slot, code, message) {
  slot.last_error_code = code;
  slot.last_error = message;
  slot.last_error_at = new Date().toISOString();
  setBridgeError(code, message);
}

function clearSlotError(slot) {
  slot.last_error_code = null;
  slot.last_error = null;
  slot.last_error_at = null;
}

function jsonError(code, message) {
  return { error: message, code };
}

function healthPayload() {
  let readyPages = 0;
  let busyPages = 0;
  let preparingPages = 0;

  for (const slot of pages.values()) {
    if (slot.ready) readyPages += 1;
    if (slot.busy) busyPages += 1;
    if (slot.preparing) preparingPages += 1;
  }

  const hasUnreadyPage = pages.size > readyPages;
  const browserConnected = !!(browser && browser.isConnected());
  const status = lastBridgeErrorCode || preparingPages > 0 || hasUnreadyPage
    ? "degraded"
    : "ok";

  return {
    status,
    pages: pages.size,
    browser_connected: browserConnected,
    max_pages: MAX_PAGES,
    ready_pages: readyPages,
    busy_pages: busyPages,
    preparing_pages: preparingPages,
    last_error_code: lastBridgeErrorCode,
    last_error: lastBridgeError,
    last_error_at: lastBridgeErrorAt,
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString()));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function respond(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return respond(res, 200, healthPayload());
  }

  if (req.method === "POST" && req.url === "/api/chat") {
    let body;
    try {
      body = await readBody(req);
    } catch (e) {
      return respond(res, 400, jsonError("invalid_json", "Invalid JSON body"));
    }

    const { sso, payload } = body;
    if (!sso || typeof sso !== "string") {
      return respond(res, 400, jsonError("invalid_request", "Missing or invalid 'sso' field"));
    }
    if (!payload || typeof payload !== "object") {
      return respond(res, 400, jsonError("invalid_request", "Missing or invalid 'payload' field"));
    }

    try {
      const result = await sendMessage(sso, payload);
      if (result.status >= 400) {
        return respond(res, result.status, {
          error: `Upstream returned ${result.status}`,
          code: "upstream_error",
          body: result.body,
        });
      }

      // Return the raw streaming body as-is (line-by-line JSON)
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Transfer-Encoding": "chunked",
      });
      res.end(result.body);
    } catch (err) {
      const bridgeErr = err instanceof BridgeError
        ? err
        : new BridgeError("page_not_prepared", err.message || "Bridge page failed");
      if (bridgeErr.code !== "page_busy") {
        setBridgeError(bridgeErr.code, bridgeErr.message);
      }
      log(`Request failed for SSO ...${sso.slice(-8)}: ${bridgeErr.message}`);
      if (bridgeErr.code !== "page_busy") {
        await destroyPage(sso);
      }
      return respond(res, bridgeErr.status, jsonError(bridgeErr.code, bridgeErr.message));
    }
  } else {
    respond(res, 404, jsonError("invalid_request", "Not found"));
  }
});

server.listen(PORT, "0.0.0.0", () => {
  log(`Browser Bridge listening on :${PORT}`);
  log(`Chromium: ${CHROMIUM_PATH}`);
  log(`Max pages: ${MAX_PAGES}, idle timeout: ${PAGE_IDLE_MS}ms`);
});

// Graceful shutdown
process.on("SIGTERM", async () => {
  log("SIGTERM received, shutting down...");
  server.close();
  if (browser) await browser.close();
  process.exit(0);
});

process.on("SIGINT", async () => {
  log("SIGINT received, shutting down...");
  server.close();
  if (browser) await browser.close();
  process.exit(0);
});
