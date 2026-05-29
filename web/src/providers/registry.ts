import type { Account, AccountExportProvider, AccountProvider } from "@/lib/api";

import { geminiProvider } from "./gemini";
import { gptProvider } from "./gpt";
import { grokProvider } from "./grok";
import type { AccountProviderDefinition, ImportProviderOption, ProviderId } from "./types";

export const accountProviderDefinitions = [gptProvider, grokProvider, geminiProvider] as const satisfies readonly AccountProviderDefinition[];

export const accountProviderRegistry: Record<ProviderId, AccountProviderDefinition> = {
  gpt: gptProvider,
  grok: grokProvider,
  gemini: geminiProvider,
};

export const accountImportProviderOptions: ImportProviderOption[] = accountProviderDefinitions.map((provider) => ({
  label: provider.label,
  value: provider.id,
}));

export const accountProviderFilterOptions: { label: string; value: AccountProvider | "all" }[] = [
  { label: "全部服务", value: "all" },
  ...accountProviderDefinitions.map((provider) => ({ label: provider.filterLabel, value: provider.id })),
];

export function normalizeAccountProvider(provider: AccountProvider | null | undefined): AccountProvider {
  const normalized = String(provider || "gpt").trim().toLowerCase();
  return normalized || "gpt";
}

export function getAccountProviderDefinition(provider: AccountProvider | null | undefined): AccountProviderDefinition {
  const normalized = normalizeAccountProvider(provider);
  return accountProviderRegistry[normalized as ProviderId] ?? {
    ...gptProvider,
    id: normalized as ProviderId,
    label: normalized,
    filterLabel: normalized,
    exportButtonLabel: `导出 ${normalized} TXT`,
    selectedExportButtonLabel: `导出所选 ${normalized} TXT`,
    refresh: {
      enabled: false,
      rowTitle: `${normalized} 账号当前不支持账号刷新`,
    },
    quota: {
      applicable: false,
      unavailableLabel: gptProvider.quota.unavailableLabel,
      unlimitedTypes: [],
    },
  };
}

export function getAccountProviderLabel(provider: AccountProvider | null | undefined) {
  return getAccountProviderDefinition(provider).label;
}

export function isProviderAccount(account: Account, provider: ProviderId) {
  return normalizeAccountProvider(account.provider) === provider;
}

export function accountProviderSupportsTokenExport(provider: AccountExportProvider, tokenCount: number) {
  const definition = getAccountProviderDefinition(provider);
  return definition.canExportWithoutTokens || tokenCount > 0;
}
