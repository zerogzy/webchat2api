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

/** @type {import('playwright').Browser | null} */
let browser = null;

/** @typedef {{ page: import('playwright').Page, context: import('playwright').BrowserContext, sso: string, ready: boolean, busy: boolean, lastUsed: number, queue: Array<{payload: object, resolve: Function, reject: Function}> }} PageSlot */

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
    log(`Failed to launch browser: ${err.message}`);
    throw err;
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
  if (slot && slot.ready) {
    slot.lastUsed = Date.now();
    return slot;
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

  // Navigate to grok.com to establish CF session
  log(`Navigating to grok.com for SSO ...${sso.slice(-8)}`);
  try {
    await page.goto("https://grok.com/", {
      waitUntil: "networkidle",
      timeout: NAV_TIMEOUT,
    });
  } catch (e) {
    log(`Navigation warning: ${e.message}`);
    // Page may still be usable even if networkidle times out
  }
  await page.waitForTimeout(2000);

  // Verify SSO is valid (x-userid cookie should be set)
  const cookies = await context.cookies("https://grok.com");
  const hasUserId = cookies.some((c) => c.name === "x-userid");
  if (!hasUserId) {
    log(`SSO ...${sso.slice(-8)} did not produce x-userid, may be invalid`);
  }

  slot.ready = true;
  log(`Page ready for SSO ...${sso.slice(-8)}`);
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
  if (slot.busy) {
    return { status: 429, body: '{"error":"Bridge page busy, retry later"}' };
  }

  slot.busy = true;
  slot.lastUsed = Date.now();

  try {
    const result = await new Promise(async (resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Bridge request timeout"));
      }, REQ_TIMEOUT);

      slot.queue.push({
        payload,
        resolve: (r) => { clearTimeout(timeout); resolve(r); },
        reject: (e) => { clearTimeout(timeout); reject(e); },
      });

      try {
        await triggerSend(slot);
      } catch (err) {
        clearTimeout(timeout);
        const idx = slot.queue.findIndex((q) => q.reject);
        if (idx >= 0) slot.queue.splice(idx, 1);
        reject(err);
      }
    });

    return result;
  } finally {
    slot.busy = false;
  }
}

async function triggerSend(slot) {
  const { page } = slot;

  try {
    await page.goto("https://grok.com/", {
      waitUntil: "networkidle",
      timeout: 30000,
    });
  } catch (e) {
    log(`Re-navigation warning: ${e.message}`);
  }

  const inputLocator = page.locator("textarea, [contenteditable]").first();
  await inputLocator.waitFor({ state: "visible", timeout: 15000 });
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
    return respond(res, 200, {
      status: "ok",
      pages: pages.size,
      browser_connected: !!(browser && browser.isConnected()),
    });
  }

  if (req.method === "POST" && req.url === "/api/chat") {
    let body;
    try {
      body = await readBody(req);
    } catch (e) {
      return respond(res, 400, { error: "Invalid JSON body" });
    }

    const { sso, payload } = body;
    if (!sso || typeof sso !== "string") {
      return respond(res, 400, { error: "Missing or invalid 'sso' field" });
    }
    if (!payload || typeof payload !== "object") {
      return respond(res, 400, { error: "Missing or invalid 'payload' field" });
    }

    try {
      const result = await sendMessage(sso, payload);
      if (result.status >= 400) {
        return respond(res, result.status, {
          error: `Upstream returned ${result.status}`,
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
      log(`Destroying broken page for SSO ...${sso.slice(-8)}: ${err.message}`);
      await destroyPage(sso);
      return respond(res, 502, { error: err.message });
    }
  } else {
    respond(res, 404, { error: "Not found" });
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
