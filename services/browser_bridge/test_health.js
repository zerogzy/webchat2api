"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const serverPath = path.join(__dirname, "server.js");
const code = fs.readFileSync(serverPath, "utf8") + `
module.exports = {
  healthPayload,
  setBridgeError,
  clearBridgeError,
  pages,
  setBrowserForTest(value) { browser = value; },
};
`;

const sandbox = {
  console: { log() {} },
  Buffer,
  Date,
  Error,
  JSON,
  Map,
  parseInt,
  Promise,
  clearTimeout,
  setTimeout,
  setInterval() { return 0; },
  process: {
    env: {},
    on() {},
    exit() { throw new Error("unexpected process.exit"); },
  },
  module: { exports: {} },
  exports: {},
  require(name) {
    if (name === "http") {
      return { createServer: () => ({ listen() {}, close() {} }) };
    }
    if (name === "playwright") {
      return { chromium: { launch: async () => ({ isConnected: () => true, on() {} }) } };
    }
    return require(name);
  },
};
sandbox.global = sandbox;
vm.runInNewContext(code, sandbox, { filename: serverPath });

const bridge = sandbox.module.exports;

bridge.setBrowserForTest({ isConnected: () => true });
bridge.setBridgeError("bridge_unavailable", "Historical launch failure");
assert.strictEqual(bridge.healthPayload().status, "degraded");
assert.strictEqual(bridge.healthPayload().last_error_code, "bridge_unavailable");
bridge.clearBridgeError();
assert.strictEqual(bridge.healthPayload().status, "ok");
assert.strictEqual(bridge.healthPayload().last_error_code, null);

bridge.pages.set("ready", {
  ready: true,
  busy: false,
  preparing: false,
});
assert.strictEqual(bridge.healthPayload().status, "ok");

bridge.pages.set("preparing", {
  ready: false,
  busy: false,
  preparing: true,
});
assert.strictEqual(bridge.healthPayload().status, "degraded");
bridge.pages.delete("preparing");

bridge.setBrowserForTest({ isConnected: () => true });
bridge.clearBridgeError();
const recovered = bridge.healthPayload();
assert.strictEqual(recovered.status, "ok");
assert.strictEqual(recovered.last_error_code, null);

console.log("Browser Bridge health recovery semantics ok");
