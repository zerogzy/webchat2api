import type { AccountProviderDefinition } from "./types";

export const geminiProvider: AccountProviderDefinition = {
  id: "gemini",
  label: "Gemini",
  filterLabel: "Gemini",
  exportFilename: "webchat2api_gemini.txt",
  exportButtonLabel: "导出 Gemini TXT",
  selectedExportButtonLabel: "导出所选 Gemini TXT",
  canExportWithoutTokens: true,
  importTokenCopy: {
    label: "Gemini Cookie 列表",
    placeholder: "每行一组 Gemini cookie/session，需包含 __Secure-1PSID，可包含 __Secure-1PSIDTS...",
    fileHelp: "支持 `.txt`，每行一组 Gemini cookie/session；需包含 __Secure-1PSID，可包含 __Secure-1PSIDTS。",
    successLabel: "Access Token 导入完成",
    submitLabel: "导入 Token",
  },
  importSessionCopy: {
    label: "Gemini cookie / session",
    placeholder: "每行一组 Gemini cookie/session，需包含 __Secure-1PSID，可包含 __Secure-1PSIDTS...",
    help: "Gemini 请粘贴每行一组 cookie/session，需包含 `__Secure-1PSID`，可包含 `__Secure-1PSIDTS`。GPT 的 Session JSON 不是 Gemini 的正确来源。",
    successLabel: "Gemini Cookie 导入完成",
    submitLabel: "导入 Gemini Cookie",
    parseJsonAccessToken: false,
  },
  importMethods: ["token", "session", "cpa", "remote-cpa", "sub2api"],
  metadataLabel: "Gemini Cookie / Session",
  accountInfoHelp: "用于 Gemini cookie/session 账号池，不用于 GPT 图像额度刷新。",
  tokenHiddenLabel: "Gemini session hidden",
  badgeVariant: "outline",
  quota: {
    applicable: false,
    unavailableLabel: "不适用",
    unlimitedTypes: [],
  },
  refresh: {
    enabled: false,
    rowTitle: "Gemini 账号当前不支持账号刷新",
  },
};
