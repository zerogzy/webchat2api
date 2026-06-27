import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const ENTRY =
  "async function ceE(){let{main:e}=await Promise.resolve().then(()=>(uQr(),CQr));await e()}ceE().catch(e=>{QeE(e)});";
const EXPORTS = "\nexport { eTA, GK, t4A, XRn };\n";
const VERSION = "1";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function packageRootFromCliBin() {
  const cliBin = process.env.QODER_CLI_BIN || findOnPath("qoderclicn");
  if (cliBin && fs.existsSync(cliBin)) {
    const real = fs.realpathSync(cliBin);
    if (real.endsWith(path.join("bundle", "qoderclicn.js"))) {
      return path.dirname(path.dirname(real));
    }
  }
  return null;
}

function findOnPath(command) {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, command);
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function packageRootFromRequire() {
  const require = createRequire(import.meta.url);
  const packageJson = require.resolve("@qodercn-ai/qoderclicn/package.json", {
    paths: [process.cwd()],
  });
  return path.dirname(packageJson);
}

function sourceBundleDir() {
  const root = packageRootFromCliBin() ?? packageRootFromRequire();
  const dir = path.join(root, "bundle");
  const runtime = path.join(dir, "qoder-worker-runtime.mjs");
  if (!fs.existsSync(runtime)) {
    throw new Error(`Qoder worker runtime was not found at ${runtime}`);
  }
  return dir;
}

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const from = path.join(src, entry.name);
    const to = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      copyDir(from, to);
    } else if (entry.isFile()) {
      fs.copyFileSync(from, to);
    }
  }
}

function patchedRuntimePath() {
  const srcDir = sourceBundleDir();
  const stat = fs.statSync(path.join(srcDir, "qoder-worker-runtime.mjs"));
  const cacheKey = `${VERSION}-${stat.size}-${Math.floor(stat.mtimeMs)}`;
  const dstDir = path.join(os.tmpdir(), "webchat2api-qoder-wasm", cacheKey);
  const dstRuntime = path.join(dstDir, "qoder-worker-runtime.mjs");
  const marker = path.join(dstDir, ".ready");
  if (fs.existsSync(marker) && fs.existsSync(dstRuntime)) {
    return dstRuntime;
  }

  const tempDir = `${dstDir}.${process.pid}.${Date.now()}`;
  fs.rmSync(tempDir, { recursive: true, force: true });
  copyDir(srcDir, tempDir);
  let code = fs.readFileSync(path.join(tempDir, "qoder-worker-runtime.mjs"), "utf8");
  if (!code.includes(ENTRY)) {
    throw new Error("Qoder worker runtime entry marker was not found");
  }
  code = code.replace(ENTRY, ENTRY.replace("ceE().catch(e=>{QeE(e)});", ""));
  code += EXPORTS;
  fs.writeFileSync(path.join(tempDir, "qoder-worker-runtime.mjs"), code);
  fs.writeFileSync(path.join(tempDir, ".ready"), "1");
  fs.rmSync(dstDir, { recursive: true, force: true });
  fs.renameSync(tempDir, dstDir);
  return dstRuntime;
}

function textContent(content) {
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") return item.text ?? "";
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return content == null ? "" : String(content);
}

function convertMessages(messages) {
  const out = [];
  for (const message of Array.isArray(messages) ? messages : []) {
    if (!message || typeof message !== "object") continue;
    const role = String(message.role || "user");
    if (role === "system" || role === "user" || role === "assistant") {
      out.push({
        role,
        content: [{ type: "text", text: textContent(message.content) }],
      });
    } else if (role === "tool") {
      out.push({
        role: "user",
        content: [{ type: "text", text: `tool_result(${message.tool_call_id || ""}): ${textContent(message.content)}` }],
      });
    }
  }
  return out.length ? out : [{ role: "user", content: [{ type: "text", text: "Reply OK only." }] }];
}

function convertTools(tools) {
  if (!Array.isArray(tools)) return [];
  return tools
    .map((tool) => {
      const fn = tool?.function;
      if (!fn?.name) return null;
      return {
        type: "function",
        function: {
          name: fn.name,
          description: fn.description || "",
          parameters: fn.parameters && typeof fn.parameters === "object" ? fn.parameters : { type: "object", properties: {} },
        },
      };
    })
    .filter(Boolean);
}

function requestBody(request) {
  const rid = crypto.randomUUID().replaceAll("-", "");
  const sid = crypto.randomUUID();
  const parameters = {};
  for (const key of ["temperature", "top_p", "max_tokens", "stop", "tool_choice", "context_length", "reasoning_effort"]) {
    if (request.body && Object.hasOwn(request.body, key)) parameters[key] = request.body[key];
  }
  return {
    request_id: rid,
    request_set_id: rid,
    chat_record_id: rid,
    session_id: sid,
    chat_session_id: sid,
    stream: true,
    chat_task: "FREE_INPUT",
    is_reply: true,
    is_retry: false,
    source: 1,
    version: "3",
    agent_id: "agent_common",
    task_id: "common",
    model_config: {
      key: request.upstream_model || "qmodel",
      display_name: request.display_name || request.upstream_model || "qmodel",
      model: "",
      format: "openai",
      is_vl: false,
      is_reasoning: true,
      api_key: "",
      url: "",
      source: request.model_source || "system",
      max_input_tokens: 200000,
    },
    system: "",
    messages: convertMessages(request.messages),
    tools: convertTools(request.body?.tools),
    parameters,
    business: { product: "qoder", type: "chat", id: rid, sub_task: "chat", stage: "default" },
  };
}

async function emitChunks(response) {
  const decoder = new TextDecoder();
  let buffer = "";
  for await (const raw of response.body) {
    buffer += decoder.decode(raw, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      for (const line of event.split(/\r?\n/)) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") continue;
        let outer;
        try {
          outer = JSON.parse(data);
        } catch {
          continue;
        }
        const body = outer && typeof outer === "object" ? outer.body : null;
        if (typeof body !== "string") continue;
        let chunk;
        try {
          chunk = JSON.parse(body);
        } catch {
          continue;
        }
        process.stdout.write(`${JSON.stringify(chunk)}\n`);
      }
    }
  }
}

async function main() {
  const request = JSON.parse(await readStdin());
  const mod = await import(pathToFileURL(patchedRuntimePath()).href);
  await mod.GK.initializeQoderRuntime({ initializeWasm: true });
  const auth = mod.t4A.getQoderAuthManager();
  await auth.initAuth({ pat: request.pat_token });
  const response = await mod.XRn({
    body: requestBody(request),
    reason: "webchat2api",
    authManager: auth,
    workdirs: [],
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Qoder WASM request failed: ${response.status} ${text}`);
  }
  await emitChunks(response);
}

main().catch((error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exit(1);
});
