import type { AccountProviderDefinition } from "./types";

export const grokProvider: AccountProviderDefinition = {
  id: "grok",
  label: "Grok",
  filterLabel: "Grok",
  exportFilename: "webchat2api_grok.txt",
  exportButtonLabel: "导出 Grok TXT",
  selectedExportButtonLabel: "导出所选 Grok TXT",
  canExportWithoutTokens: false,
  importTokenCopy: {
    label: "Grok Token / Cookie 列表",
    placeholder: "每行一个 Grok token 或 cookie，sso= 前缀可选；不要粘贴 ChatGPT Session JSON...",
    fileHelp: "支持 `.txt`，每行一个 Grok token 或 cookie；ChatGPT Session JSON 不是 Grok 的导入来源。",
    successLabel: "Access Token 导入完成",
    submitLabel: "导入 Token",
  },
  importSessionCopy: {
    label: "Grok token / cookie",
    placeholder: "每行一个 Grok token 或 cookie，sso= 前缀可选...",
    help: "Grok 请优先使用 Token 导入：当前后端支持每行一个 token 或 cookie，`sso=` 前缀可选。GPT 的 Session JSON 不是 Grok 的正确来源。",
    successLabel: "Grok Token / Cookie 导入完成",
    submitLabel: "导入 Grok Token / Cookie",
    parseJsonAccessToken: false,
  },
  importMethods: ["token", "session", "cpa", "remote-cpa", "sub2api"],
  metadataLabel: "Grok 文本模型计划/池",
  accountInfoHelp: "用于 Grok 文本模型，不用于 ChatGPT 图像生成。",
  tokenHiddenLabel: "凭据已隐藏",
  badgeVariant: "info",
  quota: {
    applicable: false,
    unavailableLabel: "不适用",
    unlimitedTypes: [],
  },
  refresh: {
    enabled: false,
    rowTitle: "Grok 账号当前不支持账号刷新",
  },
};
