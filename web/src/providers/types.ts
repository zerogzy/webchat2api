import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";

import type { Account, AccountExportProvider, AccountProvider } from "@/lib/api";

export type ProviderId = AccountExportProvider;
export type AccountImportMethod = "token" | "session" | "cpa" | "remote-cpa" | "sub2api";

export type ImportProviderOption = {
  label: string;
  value: ProviderId;
};

export type AccountImportCopy = {
  label: string;
  placeholder: string;
  fileHelp?: string;
  help?: string;
  successLabel: string;
  submitLabel: string;
  emptyMessage?: string;
  parseErrorMessage?: string;
};

export type AccountImportMethodConfig = {
  method: AccountImportMethod;
  title: string;
  description: string;
  icon?: ComponentType<LucideProps>;
  route?: string;
};

export type AccountProviderDefinition = {
  id: ProviderId;
  label: string;
  filterLabel: string;
  exportFilename: string;
  exportButtonLabel: string;
  selectedExportButtonLabel: string;
  canExportWithoutTokens: boolean;
  importTokenCopy: AccountImportCopy;
  importSessionCopy: AccountImportCopy & {
    parseJsonAccessToken: boolean;
    sessionUrl?: string;
  };
  importMethods: AccountImportMethod[];
  metadataLabel: string;
  accountInfoHelp: string;
  tokenHiddenLabel: string;
  badgeVariant: "outline" | "info";
  quota: {
    applicable: boolean;
    metricLabel?: string;
    unavailableLabel: string;
    unlimitedTypes: string[];
    unknownField?: keyof Account;
  };
  refresh: {
    enabled: boolean;
    buttonLabel?: string;
    selectedButtonLabel?: string;
    rowTitle: string;
  };
};

export type AnyAccountProvider = AccountProvider | ProviderId | string;
