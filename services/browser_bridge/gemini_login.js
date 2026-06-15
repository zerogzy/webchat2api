"use strict";

const { chromium } = require("playwright");
const crypto = require("crypto");

const CHROMIUM_PATH = process.env.CHROMIUM_PATH || "/usr/bin/chromium";
const GEMINI_LOGIN_TIMEOUT_MS = parseInt(process.env.GEMINI_LOGIN_TIMEOUT_MS || "300000", 10);
const GEMINI_JOB_RETENTION_MS = parseInt(process.env.GEMINI_JOB_RETENTION_MS || "600000", 10);
const BROWSER_ENGINE = (process.env.BROWSER_ENGINE || "playwright").trim().toLowerCase();
const NAV_TIMEOUT = 60000;
const STEP_TIMEOUT = 30000;
const REQUIRED_COOKIES = new Set(["__Secure-1PSID", "__Secure-1PSIDTS"]);
const GOOGLE_COOKIE_NAMES = new Set([
  "SID", "HSID", "SSID", "APISID", "SAPISID", "NID", "S", "SIDCC", "COMPASS", "__itrace_wid",
  "__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC", "__Secure-1PAPISID",
  "__Secure-3PSID", "__Secure-3PSIDTS", "__Secure-3PSIDCC", "__Secure-3PAPISID",
]);

const jobs = new Map();

class GeminiLoginError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "GeminiLoginError";
    this.code = code;
  }
}

function nowIso() {
  return new Date().toISOString();
}

function jobId() {
  return `gemini-login-${crypto.randomBytes(12).toString("hex")}`;
}

function publicJob(job) {
  const payload = {
    jobId: job.id,
    status: job.status,
    step: job.step,
    message: job.message,
    engine: job.engine,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    expiresAt: job.expiresAt,
  };
  if (job.allowedActions) payload.allowedActions = job.allowedActions;
  if (job.errorCode) payload.errorCode = job.errorCode;
  if (job.status === "success") {
    payload.account = job.account || null;
    payload.added = job.added || 0;
    payload.skipped = job.skipped || 0;
    payload.refreshed = job.refreshed || 0;
  }
  return payload;
}

function setJob(job, fields) {
  Object.assign(job, fields, { updatedAt: nowIso() });
}

function failJob(job, code, message) {
  setJob(job, { status: "failed", errorCode: code, message, allowedActions: [] });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForFirst(page, selectors, timeout = STEP_TIMEOUT) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const selector of selectors) {
      const locator = page.locator(selector).first();
      try {
        if (await locator.isVisible({ timeout: 500 })) return locator;
      } catch (_) {
        // 继续尝试其他选择器。
      }
    }
    await delay(300);
  }
  throw new GeminiLoginError("ELEMENT_NOT_FOUND", `未找到页面元素: ${selectors.join(", ")}`);
}

async function fillAndNext(page, selectors, value, stepName) {
  const input = await waitForFirst(page, selectors);
  await input.fill(value);
  const next = page.locator("#identifierNext button, #passwordNext button, button:has-text('Next'), button:has-text('下一步')").first();
  try {
    await next.click({ timeout: 5000 });
  } catch (_) {
    await page.keyboard.press("Enter");
  }
  await page.waitForLoadState("domcontentloaded", { timeout: NAV_TIMEOUT }).catch(() => null);
  await delay(1200);
  return stepName;
}

async function launchPlaywrightContext(options) {
  const launchOptions = {
    executablePath: CHROMIUM_PATH,
    headless: options.headless !== false,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
  };
  const browser = await chromium.launch(launchOptions);
  const contextOptions = {
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1080 },
    locale: "en-US",
    timezoneId: "Asia/Shanghai",
  };
  if (options.proxy) contextOptions.proxy = { server: options.proxy };
  const context = await browser.newContext(contextOptions);
  return { browser, context, engine: "playwright" };
}

async function launchCloakContext(options) {
  let cloak;
  try {
    cloak = await import("cloakbrowser");
  } catch (err) {
    throw new GeminiLoginError("CLOAK_NOT_INSTALLED", `CloakBrowser 未安装或无法加载: ${err.message}`);
  }
  const launchContext = cloak.launchContext || cloak.default?.launchContext;
  if (typeof launchContext !== "function") {
    throw new GeminiLoginError("CLOAK_NOT_INSTALLED", "CloakBrowser 未提供 launchContext 接口");
  }
  const context = await launchContext({
    headless: options.headless !== false,
    proxy: options.proxy || undefined,
    viewport: { width: 1920, height: 1080 },
    locale: "en-US",
    timezone: "Asia/Shanghai",
  });
  return { browser: null, context, engine: "cloak" };
}

async function launchContext(options) {
  if (BROWSER_ENGINE === "cloak") return launchCloakContext(options);
  if (BROWSER_ENGINE !== "playwright") {
    throw new GeminiLoginError("BROWSER_ENGINE_INVALID", `不支持的 BROWSER_ENGINE: ${BROWSER_ENGINE}`);
  }
  return launchPlaywrightContext(options);
}

async function closeRuntime(job) {
  const runtime = job.runtime;
  job.runtime = null;
  if (!runtime) return;
  try { await runtime.context?.close(); } catch (_) { /* ignore */ }
  try { await runtime.browser?.close(); } catch (_) { /* ignore */ }
}

function cookieMap(cookies) {
  const result = {};
  for (const cookie of cookies || []) {
    if (!cookie || !cookie.name || !cookie.value) continue;
    if (GOOGLE_COOKIE_NAMES.has(cookie.name) || cookie.domain?.includes("google.com")) {
      result[cookie.name] = cookie.value;
    }
  }
  return result;
}

function hasRequiredCookies(cookies) {
  return [...REQUIRED_COOKIES].every((name) => cookies[name]);
}

async function extractGeminiCookies(context) {
  const cookies = await context.cookies([
    "https://gemini.google.com",
    "https://accounts.google.com",
    "https://google.com",
  ]);
  const mapped = cookieMap(cookies);
  if (!hasRequiredCookies(mapped)) {
    throw new GeminiLoginError("COOKIE_NOT_FOUND", "登录完成但未找到 Gemini 必需 Cookie");
  }
  return mapped;
}

async function maybeNeedTwoFactor(page) {
  const selectors = [
    "input[type='tel']",
    "input[name='totpPin']",
    "input[name='idvPin']",
    "input[autocomplete='one-time-code']",
  ];
  for (const selector of selectors) {
    try {
      if (await page.locator(selector).first().isVisible({ timeout: 800 })) return true;
    } catch (_) {
      // 继续尝试其他选择器。
    }
  }
  const body = await page.locator("body").innerText({ timeout: 1000 }).catch(() => "");
  return /2-Step|2FA|verification code|验证码|两步验证|Google Authenticator/i.test(body);
}

async function submitTotp(page, totp) {
  await fillAndNext(page, [
    "input[type='tel']",
    "input[name='totpPin']",
    "input[name='idvPin']",
    "input[autocomplete='one-time-code']",
  ], totp, "two_factor");
}

async function detectBlockingChallenge(page) {
  for (const selector of ["input[type='password']", "input[name='Passwd']", "input[type='tel']", "input[name='totpPin']", "input[name='idvPin']", "input[autocomplete='one-time-code']"]) {
    try {
      if (await page.locator(selector).first().isVisible({ timeout: 300 })) return;
    } catch (_) {
      // 如果还能看到密码或验证码输入框，就不是阻塞挑战。
    }
  }
  const text = await page.locator("body").innerText({ timeout: 2000 }).catch(() => "");
  if (/captcha|recaptcha|验证码/i.test(text)) {
    throw new GeminiLoginError("CAPTCHA_REQUIRED", "Google 要求验证码，当前无头登录无法自动完成");
  }
  if (/security key|tap yes|try another way|check your phone|open the gmail app|恢复邮箱|安全验证|确认是你本人/i.test(text)) {
    throw new GeminiLoginError("SECURITY_CHALLENGE_REQUIRED", "Google 要求额外安全验证，需要人工处理或更换代理后重试");
  }
  if (/wrong password|couldn't sign you in|无法登录|密码错误/i.test(text)) {
    throw new GeminiLoginError("INVALID_CREDENTIALS", "Google 账号或密码错误");
  }
}

async function waitForGeminiReady(page) {
  await page.goto("https://gemini.google.com/app", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
  await delay(3000);
  const text = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
  if (/sign in|登录|accounts\.google\.com/i.test(text) && page.url().includes("accounts.google.com")) {
    throw new GeminiLoginError("LOGIN_NOT_COMPLETED", "Google 登录未完成，Gemini 页面仍要求登录");
  }
}

async function runLogin(job) {
  const timeout = setTimeout(() => {
    if (["running", "waiting_for_2fa", "waiting_for_manual_confirmation"].includes(job.status)) {
      failJob(job, "LOGIN_TIMEOUT", "Gemini 浏览器登录超时");
      closeRuntime(job);
    }
  }, job.timeoutMs);

  try {
    setJob(job, { status: "running", step: "launch_browser", message: "正在启动浏览器" });
    const runtime = await launchContext(job.options);
    job.runtime = runtime;
    job.engine = runtime.engine;
    const page = await runtime.context.newPage();
    job.page = page;

    await page.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });

    setJob(job, { step: "open_google_login", message: "正在打开 Google 登录页" });
    await page.goto("https://accounts.google.com/signin/v2/identifier?service=mail", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });

    setJob(job, { step: "enter_email", message: "正在输入邮箱" });
    await fillAndNext(page, ["input[type='email']", "input[name='identifier']"], job.options.email, "enter_email");
    await detectBlockingChallenge(page);

    setJob(job, { step: "enter_password", message: "正在输入密码" });
    await fillAndNext(page, ["input[type='password']", "input[name='Passwd']"], job.options.password, "enter_password");
    await detectBlockingChallenge(page);

    if (await maybeNeedTwoFactor(page)) {
      if (job.options.totp) {
        setJob(job, { step: "two_factor", message: "正在提交 2FA 验证码" });
        await submitTotp(page, job.options.totp);
      } else {
        setJob(job, {
          status: "waiting_for_2fa",
          step: "two_factor",
          message: "需要输入 Google 2FA 验证码",
          allowedActions: ["submit_totp", "cancel"],
        });
        await waitForContinue(job);
      }
    }

    await detectBlockingChallenge(page);
    setJob(job, { status: "running", step: "open_gemini", message: "正在打开 Gemini" });
    await waitForGeminiReady(page);

    setJob(job, { step: "extract_cookies", message: "正在提取 Gemini Cookie" });
    const cookies = await extractGeminiCookies(runtime.context);
    setJob(job, {
      status: "success",
      step: "done",
      message: "Gemini 浏览器登录成功",
      cookies,
      allowedActions: [],
    });
  } catch (err) {
    const code = err instanceof GeminiLoginError ? err.code : "LOGIN_FAILED";
    failJob(job, code, err.message || "Gemini 浏览器登录失败");
  } finally {
    clearTimeout(timeout);
    await closeRuntime(job);
  }
}

async function waitForContinue(job) {
  while (job.status === "waiting_for_2fa") {
    const action = await new Promise((resolve) => {
      job.continueResolve = resolve;
    });
    job.continueResolve = null;
    if (!action || action.action === "cancel") {
      throw new GeminiLoginError("JOB_CANCELLED", "Gemini 浏览器登录已取消");
    }
    if (action.action === "submit_totp" && action.totp) {
      setJob(job, { status: "running", step: "two_factor", message: "正在提交 2FA 验证码", allowedActions: [] });
      await submitTotp(job.page, action.totp);
      return;
    }
  }
}

function startGeminiLoginJob(payload) {
  const email = String(payload.email || "").trim();
  const password = String(payload.password || "");
  if (!email || !password) {
    throw new GeminiLoginError("INVALID_REQUEST", "email 和 password 不能为空");
  }
  const id = jobId();
  const timeoutMs = Math.max(30000, Math.min(parseInt(payload.timeoutMs || GEMINI_LOGIN_TIMEOUT_MS, 10), 900000));
  const job = {
    id,
    status: "running",
    step: "created",
    message: "Gemini 浏览器登录任务已创建",
    engine: BROWSER_ENGINE,
    options: {
      email,
      password,
      totp: String(payload.totp || "").trim(),
      proxy: String(payload.proxy || "").trim(),
      headless: payload.headless !== false,
    },
    timeoutMs,
    createdAt: nowIso(),
    updatedAt: nowIso(),
    expiresAt: new Date(Date.now() + timeoutMs + GEMINI_JOB_RETENTION_MS).toISOString(),
    allowedActions: [],
    runtime: null,
    page: null,
  };
  jobs.set(id, job);
  runLogin(job);
  return publicJob(job);
}

function getGeminiLoginJob(id, includeCookies = false) {
  const job = jobs.get(id);
  if (!job) return null;
  const payload = publicJob(job);
  if (includeCookies && job.status === "success") payload.cookies = job.cookies || {};
  return payload;
}

function continueGeminiLoginJob(id, payload) {
  const job = jobs.get(id);
  if (!job) return null;
  if (job.status !== "waiting_for_2fa") return publicJob(job);
  const action = { action: String(payload.action || "").trim(), totp: String(payload.totp || "").trim() };
  if (typeof job.continueResolve === "function") job.continueResolve(action);
  return publicJob(job);
}

async function cancelGeminiLoginJob(id) {
  const job = jobs.get(id);
  if (!job) return null;
  setJob(job, { status: "cancelled", step: "cancelled", message: "Gemini 浏览器登录已取消", allowedActions: [] });
  if (typeof job.continueResolve === "function") job.continueResolve({ action: "cancel" });
  await closeRuntime(job);
  return publicJob(job);
}

setInterval(async () => {
  const now = Date.now();
  for (const [id, job] of jobs) {
    if (Date.parse(job.expiresAt) <= now) {
      await closeRuntime(job);
      jobs.delete(id);
    }
  }
}, 60000);

module.exports = {
  GeminiLoginError,
  startGeminiLoginJob,
  getGeminiLoginJob,
  continueGeminiLoginJob,
  cancelGeminiLoginJob,
};
