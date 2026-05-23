"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Plus, SendHorizonal, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ImageComposer } from "@/app/image/components/image-composer";
import { ImageResults, type ImageLightboxItem } from "@/app/image/components/image-results";
import { ImageSidebar } from "@/app/image/components/image-sidebar";
import { ImageLightbox } from "@/components/image-lightbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  createChatCompletion,
  createImageEditTask,
  createImageGenerationTask,
  fetchAccounts,
  fetchImageTasks,
  fetchModels,
  type Account,
  type ImageTask,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";
import {
  clearImageConversations,
  deleteImageConversation,
  getImageConversationStats,
  listImageConversations,
  renameImageConversation,
  saveImageConversation,
  saveImageConversations,
  type ImageConversation,
  type ImageConversationMode,
  type ImageTurn,
  type ImageTurnStatus,
  type StoredImage,
  type StoredReferenceImage,
} from "@/store/image-conversations";
import {
  clearTextMessages,
  listTextMessages,
  saveTextMessages,
  textMessagesToChatMessages,
  type TextChatMessage,
} from "@/store/text-conversations";

const ACTIVE_CONVERSATION_STORAGE_KEY = "webchat2api:image_active_conversation_id";
const IMAGE_SIZE_STORAGE_KEY = "webchat2api:image_last_size";
const IMAGE_COUNT_STORAGE_KEY = "webchat2api:image_last_count";
const IMAGE_MODEL_STORAGE_KEY = "webchat2api:image_last_model";
const TEXT_MODEL_STORAGE_KEY = "webchat2api:text_last_model";
const FALLBACK_IMAGE_MODELS = ["gpt-image-2", "codex-gpt-image-2"];
const FALLBACK_TEXT_MODELS = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-3.5-turbo"];
const FALLBACK_GROK_TEXT_MODELS = ["grok-4.3"];
const IMAGE_MODEL_KEYWORDS = ["image", "dall-e", "gpt-image", "codex-gpt-image"];
const KNOWN_TEXT_MODEL_PREFIXES = ["gpt-", "grok-"];

type ExperimentMode = "text" | "image";
type TextModelTestStatus = "pending" | "testing" | "success" | "error";

type TextModelTestResult = {
  model: string;
  status: TextModelTestStatus;
  message: string;
};


function clampImageCount(value: string) {
  return String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}
const activeConversationQueueIds = new Set<string>();

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  if (trimmed.length <= 12) {
    return trimmed;
  }
  return `${trimmed.slice(0, 12)}...`;
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatAvailableQuota(accounts: Account[]) {
  const availableAccounts = accounts.filter((account) => account.status !== "禁用");
  return String(availableAccounts.reduce((sum, account) => sum + Math.max(0, account.quota), 0));
}

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}

function dataUrlToFile(dataUrl: string, fileName: string, mimeType?: string) {
  const [header, content] = dataUrl.split(",", 2);
  const matchedMimeType = header.match(/data:(.*?);base64/)?.[1];
  const binary = atob(content || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], fileName, { type: mimeType || matchedMimeType || "image/png" });
}

function buildReferenceImageFromResult(image: StoredImage, fileName: string): StoredReferenceImage | null {
  if (!image.b64_json) {
    return null;
  }

  return {
    name: fileName,
    type: "image/png",
    dataUrl: `data:image/png;base64,${image.b64_json}`,
  };
}

async function fetchImageAsFile(url: string, fileName: string) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("读取结果图失败");
  }
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type || "image/png" });
}

async function buildReferenceImageFromStoredImage(image: StoredImage, fileName: string) {
  const direct = buildReferenceImageFromResult(image, fileName);
  if (direct) {
    return {
      referenceImage: direct,
      file: dataUrlToFile(direct.dataUrl, direct.name, direct.type),
    };
  }

  if (!image.url) {
    return null;
  }
  const file = await fetchImageAsFile(image.url, fileName);
  return {
    referenceImage: {
      name: file.name,
      type: file.type || "image/png",
      dataUrl: await readFileAsDataUrl(file),
    },
    file,
  };
}

function taskDataToStoredImage(image: StoredImage, task: ImageTask): StoredImage {
  if (task.status === "success") {
    const first = task.data?.[0];
    if (!first?.b64_json && !first?.url) {
      return {
        ...image,
        taskId: task.id,
        status: "error",
        error: "未返回图片数据",
      };
    }
    return {
      ...image,
      taskId: task.id,
      status: "success",
      b64_json: first.b64_json,
      url: first.url,
      revised_prompt: first.revised_prompt,
      error: undefined,
    };
  }

  if (task.status === "error") {
    return {
      ...image,
      taskId: task.id,
      status: "error",
      error: task.error || "生成失败",
    };
  }

  return {
    ...image,
    taskId: task.id,
    status: "loading",
    error: undefined,
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function pickFallbackConversationId(conversations: ImageConversation[]) {
  const activeConversation = conversations.find((conversation) =>
    conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"),
  );
  return activeConversation?.id ?? conversations[0]?.id ?? null;
}

function uniqueModelIds(items: string[]) {
  const seen = new Set<string>();
  const models: string[] = [];
  for (const item of items) {
    const model = item.trim();
    if (!model || seen.has(model)) {
      continue;
    }
    seen.add(model);
    models.push(model);
  }
  return models;
}

function isImageModel(model: string) {
  const normalized = model.toLowerCase();
  return IMAGE_MODEL_KEYWORDS.some((keyword) => normalized.includes(keyword));
}

function isTextModel(model: string) {
  const normalized = model.toLowerCase();
  return !isImageModel(normalized) || KNOWN_TEXT_MODEL_PREFIXES.some((prefix) => normalized.startsWith(prefix));
}

function pickStoredModel(storageKey: string, fallback: string) {
  if (typeof window === "undefined") {
    return fallback;
  }
  return window.localStorage.getItem(storageKey) || fallback;
}

function ModelSelect({
  value,
  models,
  label,
  onChange,
}: {
  value: string;
  models: string[];
  label: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="min-w-0 space-y-2">
      <label className="text-xs font-semibold tracking-[0.14em] text-stone-500 uppercase">{label}</label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-11 rounded-2xl border-stone-200 bg-white/90 text-stone-800 shadow-none">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {models.map((model) => (
            <SelectItem key={model} value={model}>
              {model}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function ExperimentModeSwitch({
  mode,
  onChange,
}: {
  mode: ExperimentMode;
  onChange: (mode: ExperimentMode) => void;
}) {
  return (
    <div className="inline-flex rounded-full border border-white/80 bg-white/65 p-1 shadow-[var(--shadow-soft)] backdrop-blur-sm">
      {[
        { value: "text" as const, label: "文本试验" },
        { value: "image" as const, label: "图像试验" },
      ].map((item) => (
        <button
          key={item.value}
          type="button"
          className={cn(
            "rounded-full px-4 py-2 text-sm font-medium transition",
            mode === item.value ? "bg-stone-900 text-white shadow-[0_12px_28px_-18px_rgba(68,64,60,0.9)]" : "text-stone-500 hover:bg-white/60 hover:text-stone-900",
          )}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function TextExperimentPanel({
  prompt,
  messages,
  model,
  models,
  isLoading,
  isTestingModels,
  modelTestResults,
  onPromptChange,
  onModelChange,
  onSubmit,
  onTestModels,
  onClearMessages,
}: {
  prompt: string;
  messages: TextChatMessage[];
  model: string;
  models: string[];
  isLoading: boolean;
  isTestingModels: boolean;
  modelTestResults: TextModelTestResult[];
  onPromptChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onTestModels: () => void | Promise<void>;
  onClearMessages: () => void | Promise<void>;
}) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages, isLoading]);

  return (
    <section className="grid min-h-0 flex-1 gap-3 overflow-hidden lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex min-h-0 flex-col overflow-hidden rounded-[28px] border border-white/80 bg-white/82 shadow-[var(--shadow-soft)] backdrop-blur-sm">
        <div className="border-b border-stone-200/70 px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Text to Text</div>
              <h2 className="mt-1 text-xl font-semibold tracking-tight text-stone-950">文生文对话试验</h2>
              <p className="mt-1.5 max-w-xl text-sm leading-6 text-stone-500">
                像聊天窗口一样连续验证 /v1/chat/completions，刷新页面后仍会保留当前对话。
              </p>
            </div>
            <span className="rounded-full border border-stone-200 bg-white px-3 py-1 text-xs font-medium text-stone-500">
              {messages.length} 条消息
            </span>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,rgba(250,247,239,0.9),rgba(255,253,248,0.84))] p-4 sm:p-5">
          {messages.length > 0 ? (
            <div className="flex flex-col gap-4">
              {messages.map((message) => {
                const isUser = message.role === "user";
                const isError = message.role === "error";
                return (
                  <div key={message.id} className={cn("flex", isUser ? "justify-end" : "justify-start")}>
                    <div
                      className={cn(
                        "max-w-[min(82%,760px)] rounded-[22px] px-4 py-3 text-sm leading-7 shadow-[0_18px_48px_-36px_rgba(15,23,42,0.5)]",
                        isUser
                          ? "rounded-br-md bg-primary text-primary-foreground"
                          : isError
                            ? "rounded-bl-md border border-rose-200 bg-rose-50 text-rose-700"
                            : "rounded-bl-md border border-stone-200 bg-white text-stone-800",
                      )}
                    >
                      <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-[0.14em] uppercase opacity-70">
                        <span>{isUser ? "You" : isError ? "Error" : "Assistant"}</span>
                        {!isUser && message.model ? <span>{message.model}</span> : null}
                      </div>
                      <div className="whitespace-pre-wrap">{message.content}</div>
                    </div>
                  </div>
                );
              })}
              {isLoading ? (
                <div className="flex justify-start">
                  <div className="inline-flex items-center gap-2 rounded-[22px] rounded-bl-md border border-stone-200 bg-white px-4 py-3 text-sm text-stone-500 shadow-[0_18px_48px_-36px_rgba(15,23,42,0.5)]">
                    <LoaderCircle className="size-4 animate-spin" />
                    正在等待模型响应...
                  </div>
                </div>
              ) : null}
              <div ref={messagesEndRef} />
            </div>
          ) : (
            <div className="flex h-full min-h-[260px] items-center justify-center rounded-[22px] border border-dashed border-stone-200 bg-white/70 px-6 text-center">
              <div>
                <div className="text-xs font-semibold tracking-[0.18em] text-stone-400 uppercase">Empty Thread</div>
                <p className="mt-2 text-lg font-semibold text-stone-900">从底部输入第一条文本提示</p>
                <p className="mt-2 max-w-md text-sm leading-6 text-stone-500">
                  每次发送都会保留在当前聊天窗口中，便于连续比较提示词和模型输出。
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-stone-200/70 bg-white/95 p-3 sm:p-4">
          <div className="rounded-[22px] border border-stone-200 bg-stone-50/80 p-2 shadow-sm">
            <Textarea
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  void onSubmit();
                }
              }}
              placeholder="输入要发送给模型的文本，例如：用三句话总结这个服务的用途。"
              className="min-h-[92px] resize-none rounded-[18px] border-transparent bg-white px-4 py-3 text-[15px] leading-7 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:border-stone-200 focus-visible:ring-stone-300"
            />
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 flex-1 sm:max-w-[320px]">
                <ModelSelect value={model} models={models} label="模型" onChange={onModelChange} />
              </div>
              <div className="flex items-center justify-between gap-3 sm:justify-end">
                <p className="text-xs text-stone-400">Ctrl/⌘ + Enter 发送</p>
                <Button
                  className="h-10 rounded-full bg-stone-950 px-5 text-white hover:bg-stone-800"
                  disabled={!prompt.trim() || isLoading}
                  onClick={() => void onSubmit()}
                >
                  {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <SendHorizonal className="size-4" />}
                  发送
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <aside className="min-h-0 rounded-[24px] border border-stone-200/80 bg-white/80 p-3 shadow-[0_22px_80px_-54px_rgba(15,23,42,0.45)] sm:p-4">
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between lg:flex-col lg:items-start">
            <div>
              <div className="text-xs font-semibold tracking-[0.16em] text-stone-500 uppercase">Text History</div>
              <p className="mt-1 text-sm leading-6 text-stone-600">聊天记录会保存在本机浏览器，可随时清空。</p>
            </div>
            <Button
              variant="outline"
              className="h-9 rounded-full border-stone-200 bg-white px-4 text-stone-700 hover:bg-stone-100"
              disabled={messages.length === 0 || isLoading}
              onClick={() => void onClearMessages()}
            >
              <Trash2 className="size-4" />
              清空聊天
            </Button>
          </div>

          <div className="my-4 h-px bg-stone-200/80" />

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between lg:flex-col lg:items-start">
            <div>
              <div className="text-xs font-semibold tracking-[0.16em] text-stone-500 uppercase">Model Availability</div>
              <p className="mt-1 text-sm leading-6 text-stone-600">逐个发送小提示词，显示每个文生文模型是否可用。</p>
            </div>
            <Button
              variant="outline"
              className="h-9 rounded-full border-stone-200 bg-white px-4 text-stone-700 hover:bg-stone-100"
              disabled={models.length === 0 || isTestingModels}
              onClick={() => void onTestModels()}
            >
              {isTestingModels ? <LoaderCircle className="size-4 animate-spin" /> : null}
              批量测试模型
            </Button>
          </div>
          <div className="mt-3 grid max-h-[240px] gap-2 overflow-y-auto pr-1 lg:max-h-none lg:flex-1">
            {modelTestResults.length > 0 ? (
              modelTestResults.map((item) => (
                <div
                  key={item.model}
                  className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm"
                >
                  <div className="truncate font-medium text-stone-900">{item.model}</div>
                  <div className={cn("mt-1 text-xs font-semibold", getModelTestStatusClassName(item.status))}>
                    {getModelTestStatusLabel(item.status)} · {item.message}
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-stone-200 bg-white/70 px-3 py-3 text-sm leading-6 text-stone-400">
                点击批量测试后会显示 pending / testing / success / error 状态。
              </div>
            )}
          </div>
        </div>
      </aside>
    </section>
  );
}

function getModelTestStatusLabel(status: TextModelTestStatus) {
  if (status === "testing") {
    return "testing";
  }
  if (status === "success") {
    return "success";
  }
  if (status === "error") {
    return "error";
  }
  return "pending";
}

function getModelTestStatusClassName(status: TextModelTestStatus) {
  if (status === "testing") {
    return "text-amber-600";
  }
  if (status === "success") {
    return "text-emerald-600";
  }
  if (status === "error") {
    return "text-rose-600";
  }
  return "text-stone-400";
}

function sortImageConversations(conversations: ImageConversation[]) {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function deriveTurnStatus(turn: ImageTurn): Pick<ImageTurn, "status" | "error"> {
  const loadingCount = turn.images.filter((image) => image.status === "loading").length;
  const failedCount = turn.images.filter((image) => image.status === "error").length;
  const successCount = turn.images.filter((image) => image.status === "success").length;
  if (loadingCount > 0) {
    return { status: turn.status === "queued" ? "queued" : "generating", error: undefined };
  }
  if (failedCount > 0) {
    return { status: "error", error: `其中 ${failedCount} 张未成功生成` };
  }
  if (successCount > 0) {
    return { status: "success", error: undefined };
  }
  return { status: "queued", error: undefined };
}

async function syncConversationImageTasks(items: ImageConversation[]) {
  const taskIds = Array.from(
    new Set(
      items.flatMap((conversation) =>
        conversation.turns.flatMap((turn) =>
          turn.resultsDeleted
            ? []
            : turn.images.flatMap((image) => (image.status === "loading" && image.taskId ? [image.taskId] : [])),
        ),
      ),
    ),
  );
  if (taskIds.length === 0) {
    return items;
  }

  let taskList: Awaited<ReturnType<typeof fetchImageTasks>>;
  try {
    taskList = await fetchImageTasks(taskIds);
  } catch {
    return items;
  }
  const taskMap = new Map(taskList.items.map((task) => [task.id, task]));
  let changed = false;
  const normalized = items.map((conversation) => {
    const turns = conversation.turns.map((turn) => {
      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (image.status !== "loading" || !image.taskId) {
          return image;
        }
        const task = taskMap.get(image.taskId);
        if (!task) {
          return image;
        }
        const nextImage = taskDataToStoredImage(image, task);
        if (nextImage !== image) {
          turnChanged = true;
        }
        return nextImage;
      });
      if (!turnChanged) {
        return turn;
      }
      changed = true;
      const derived = deriveTurnStatus({ ...turn, images });
      return {
        ...turn,
        ...derived,
        images,
      };
    });
    if (turns === conversation.turns || !turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }
    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }
  return normalized;
}

async function recoverConversationHistory(items: ImageConversation[]) {
  let changed = false;
  const normalized = items.map((conversation) => {
    const turns = conversation.turns.map((turn) => {
      if (turn.status !== "queued" && turn.status !== "generating") {
        return turn;
      }

      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (image.status !== "loading" || image.taskId) {
          return image;
        }
        turnChanged = true;
        return {
          ...image,
          status: "error" as const,
          error: "页面刷新或任务中断，未找到可恢复的任务 ID",
        };
      });
      const derived = deriveTurnStatus({ ...turn, images });
      if (!turnChanged && derived.status === turn.status && derived.error === turn.error) {
        return turn;
      }
      changed = true;
      return {
        ...turn,
        ...derived,
        images,
      };
    });

    if (!turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }

    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }

  return syncConversationImageTasks(normalized);
}


function ImagePageContent({ isAdmin }: { isAdmin: boolean }) {
  const didLoadQuotaRef = useRef(false);
  const conversationsRef = useRef<ImageConversation[]>([]);
  const resultsViewportRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [imagePrompt, setImagePrompt] = useState("");
  const [imageModel, setImageModel] = useState(FALLBACK_IMAGE_MODELS[0]);
  const [textPrompt, setTextPrompt] = useState("");
  const [textModel, setTextModel] = useState(FALLBACK_TEXT_MODELS[0]);
  const [textMessages, setTextMessages] = useState<TextChatMessage[]>([]);
  const [isLoadingTextHistory, setIsLoadingTextHistory] = useState(true);
  const [isSubmittingText, setIsSubmittingText] = useState(false);
  const [isTestingTextModels, setIsTestingTextModels] = useState(false);
  const [textModelTestResults, setTextModelTestResults] = useState<TextModelTestResult[]>([]);
  const [experimentMode, setExperimentMode] = useState<ExperimentMode>("text");
  const [modelIds, setModelIds] = useState<string[]>([]);
  const [imageCount, setImageCount] = useState("1");
  const [imageSize, setImageSize] = useState("");
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [referenceImageFiles, setReferenceImageFiles] = useState<File[]>([]);
  const [referenceImages, setReferenceImages] = useState<StoredReferenceImage[]>([]);
  const [conversations, setConversations] = useState<ImageConversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [availableQuota, setAvailableQuota] = useState("加载中...");
  const [lightboxImages, setLightboxImages] = useState<ImageLightboxItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [deleteConfirm, setDeleteConfirm] = useState<
    | { type: "one"; id: string }
    | { type: "prompt"; conversationId: string; turnId: string }
    | { type: "results"; conversationId: string; turnId: string }
    | { type: "all" }
    | null
  >(null);

  const parsedCount = useMemo(() => Number(clampImageCount(imageCount)), [imageCount]);
  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );
  const imageModelOptions = useMemo(() => {
    const discovered = modelIds.filter(isImageModel);
    return uniqueModelIds([...discovered, ...FALLBACK_IMAGE_MODELS, imageModel]);
  }, [imageModel, modelIds]);
  const textModelOptions = useMemo(() => {
    const discovered = modelIds.filter(isTextModel);
    const grokFallbacks = modelIds.length === 0 ? FALLBACK_GROK_TEXT_MODELS : [];
    return uniqueModelIds([...discovered, ...FALLBACK_TEXT_MODELS, ...grokFallbacks, textModel]);
  }, [modelIds, textModel]);
  const activeTaskCount = useMemo(
    () =>
      conversations.reduce((sum, conversation) => {
        const stats = getImageConversationStats(conversation);
        return sum + stats.queued + stats.running;
      }, 0),
    [conversations],
  );
  const deleteConfirmTitle =
    deleteConfirm?.type === "all"
      ? "清空历史记录"
      : deleteConfirm?.type === "prompt"
        ? "删除提示词记录"
        : deleteConfirm?.type === "results"
          ? "删除生成结果"
          : deleteConfirm?.type === "one"
            ? "删除对话"
            : "";
  const deleteConfirmDescription =
    deleteConfirm?.type === "all"
      ? "确认删除全部图片历史记录吗？删除后无法恢复。"
      : deleteConfirm?.type === "prompt"
        ? "确认删除这条提示词记录吗？对应生成结果会保留。"
        : deleteConfirm?.type === "results"
          ? "确认删除这条生成结果吗？对应提示词记录会保留。"
          : deleteConfirm?.type === "one"
            ? "确认删除这条图片对话吗？删除后无法恢复。"
            : "";

  useEffect(() => {
    let cancelled = false;

    const loadModels = async () => {
      try {
        const data = await fetchModels();
        if (cancelled) {
          return;
        }
        const nextModelIds = uniqueModelIds(data.data.map((model) => model.id));
        setModelIds(nextModelIds);
        const imageModels = nextModelIds.filter(isImageModel);
        const textModels = nextModelIds.filter(isTextModel);
        setImageModel((current) => (imageModels.includes(current) ? current : imageModels[0] || current));
        setTextModel((current) => (textModels.includes(current) ? current : textModels[0] || current));
      } catch {
        if (!cancelled) {
          setModelIds([]);
        }
      }
    };

    void loadModels();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && imageModel) {
      window.localStorage.setItem(IMAGE_MODEL_STORAGE_KEY, imageModel);
    }
  }, [imageModel]);

  useEffect(() => {
    if (typeof window !== "undefined" && textModel) {
      window.localStorage.setItem(TEXT_MODEL_STORAGE_KEY, textModel);
    }
  }, [textModel]);

  useEffect(() => {
    let cancelled = false;

    const loadTextHistory = async () => {
      try {
        const messages = await listTextMessages();
        if (!cancelled) {
          setTextMessages(messages);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取文本聊天记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingTextHistory(false);
        }
      }
    };

    void loadTextHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isLoadingTextHistory) {
      void saveTextMessages(textMessages);
    }
  }, [isLoadingTextHistory, textMessages]);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      try {
        const storedSize = typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_SIZE_STORAGE_KEY) : null;
        const storedCount = typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_COUNT_STORAGE_KEY) : null;
        const storedImageModel = pickStoredModel(IMAGE_MODEL_STORAGE_KEY, FALLBACK_IMAGE_MODELS[0]);
        const storedTextModel = pickStoredModel(TEXT_MODEL_STORAGE_KEY, FALLBACK_TEXT_MODELS[0]);
        setImageSize(storedSize || "");
        setImageCount(storedCount ? clampImageCount(storedCount) : "1");
        setImageModel(storedImageModel);
        setTextModel(storedTextModel);

        const items = await listImageConversations();
        const normalizedItems = await recoverConversationHistory(items);
        if (cancelled) {
          return;
        }

        conversationsRef.current = normalizedItems;
        setConversations(normalizedItems);
        const storedConversationId =
          typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY) : null;
        const nextSelectedConversationId =
          (storedConversationId && normalizedItems.some((conversation) => conversation.id === storedConversationId)
            ? storedConversationId
            : null) ?? pickFallbackConversationId(normalizedItems);
        setSelectedConversationId(nextSelectedConversationId);
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取会话记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    };

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadQuota = useCallback(async () => {
    if (!isAdmin) {
      setAvailableQuota("--");
      return;
    }
    try {
      const data = await fetchAccounts();
      setAvailableQuota(formatAvailableQuota(data.items));
    } catch {
      setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
    }
  }, [isAdmin]);

  useEffect(() => {
    if (didLoadQuotaRef.current) {
      return;
    }
    didLoadQuotaRef.current = true;

    const handleFocus = () => {
      void loadQuota();
    };

    void loadQuota();
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [isAdmin, loadQuota]);

  useEffect(() => {
    if (!selectedConversation) {
      return;
    }

    resultsViewportRef.current?.scrollTo({
      top: resultsViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [selectedConversation?.updatedAt, selectedConversation?.turns.length, selectedConversation]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (selectedConversationId) {
      window.localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, selectedConversationId);
    } else {
      window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    }
  }, [selectedConversationId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (imageSize) {
      window.localStorage.setItem(IMAGE_SIZE_STORAGE_KEY, imageSize);
      return;
    }
    window.localStorage.removeItem(IMAGE_SIZE_STORAGE_KEY);
  }, [imageSize]);

  useEffect(() => {
    if (typeof window !== "undefined" && parsedCount > 0) {
      window.localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, String(parsedCount));
    }
  }, [parsedCount]);

  useEffect(() => {
    if (selectedConversationId && !conversations.some((conversation) => conversation.id === selectedConversationId)) {
      setSelectedConversationId(pickFallbackConversationId(conversations));
    }
  }, [conversations, selectedConversationId]);

  const persistConversation = async (conversation: ImageConversation) => {
    const nextConversations = sortImageConversations([
      conversation,
      ...conversationsRef.current.filter((item) => item.id !== conversation.id),
    ]);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    await saveImageConversation(conversation);
  };

  const updateConversation = useCallback(
    async (
      conversationId: string,
      updater: (current: ImageConversation | null) => ImageConversation,
      options: { persist?: boolean } = {},
    ) => {
      const current = conversationsRef.current.find((item) => item.id === conversationId) ?? null;
      const nextConversation = updater(current);
      const nextConversations = sortImageConversations([
        nextConversation,
        ...conversationsRef.current.filter((item) => item.id !== conversationId),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      if (options.persist !== false) {
        await saveImageConversation(nextConversation);
      }
    },
    [],
  );

  const clearComposerInputs = useCallback(() => {
    setImagePrompt("");
    setReferenceImageFiles([]);
    setReferenceImages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, []);

  const resetComposer = useCallback(() => {
    clearComposerInputs();
  }, [clearComposerInputs]);

  const handleCreateDraft = () => {
    setSelectedConversationId(null);
    resetComposer();
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (id: string) => {
    const nextConversations = conversations.filter((item) => item.id !== id);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    if (selectedConversationId === id) {
      setSelectedConversationId(pickFallbackConversationId(nextConversations));
      resetComposer();
    }

    try {
      await deleteImageConversation(id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const items = await listImageConversations();
      conversationsRef.current = items;
      setConversations(items);
    }
  };

  const handleDeleteTurnPart = async (conversationId: string, turnId: string, part: "prompt" | "results") => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    if (!conversation) {
      return;
    }

    const turns = conversation.turns
      .map((turn) => {
        if (turn.id !== turnId) {
          return turn;
        }
        const nextTurn = {
          ...turn,
          prompt: part === "prompt" ? "" : turn.prompt,
          promptDeleted: part === "prompt" ? true : turn.promptDeleted,
          resultsDeleted: part === "results" ? true : turn.resultsDeleted,
          status: part === "results" && turn.status === "generating" ? "error" as const : turn.status,
          images:
            part === "results"
              ? turn.images.map((image) => ({ id: image.id, status: "error" as const, error: "生成结果已删除" }))
              : turn.images,
        };
        return nextTurn.promptDeleted && nextTurn.resultsDeleted ? null : nextTurn;
      })
      .filter((turn): turn is ImageTurn => Boolean(turn));

    if (turns.length === 0) {
      await handleDeleteConversation(conversationId);
      return;
    }

    const nextConversation = {
      ...conversation,
      updatedAt: new Date().toISOString(),
      turns,
    };
    await persistConversation(nextConversation);
  };

  const handleClearHistory = async () => {
    try {
      await clearImageConversations();
      conversationsRef.current = [];
      setConversations([]);
      setSelectedConversationId(null);
      resetComposer();
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  };

  const handleRenameConversation = async (id: string, title: string) => {
    const nextConversations = conversations.map((item) =>
      item.id === id ? { ...item, title, updatedAt: new Date().toISOString() } : item,
    );
    conversationsRef.current = sortImageConversations(nextConversations);
    setConversations(conversationsRef.current);
    try {
      await renameImageConversation(id, title);
    } catch (error) {
      const message = error instanceof Error ? error.message : "重命名失败";
      toast.error(message);
    }
  };

  const openDeleteConversationConfirm = (id: string) => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "one", id });
  };

  const openDeletePromptConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "prompt", conversationId, turnId });
  };

  const openDeleteResultsConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "results", conversationId, turnId });
  };

  const openClearHistoryConfirm = () => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "all" });
  };

  const handleConfirmDelete = async () => {
    const target = deleteConfirm;
    setDeleteConfirm(null);
    if (!target) {
      return;
    }
    if (target.type === "all") {
      await handleClearHistory();
      return;
    }
    if (target.type === "prompt" || target.type === "results") {
      await handleDeleteTurnPart(target.conversationId, target.turnId, target.type);
      return;
    }
    await handleDeleteConversation(target.id);
  };

  const appendReferenceImages = useCallback(async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    try {
      const previews = await Promise.all(
        files.map(async (file) => ({
          name: file.name,
          type: file.type || "image/png",
          dataUrl: await readFileAsDataUrl(file),
        })),
      );

      setReferenceImageFiles((prev) => [...prev, ...files]);
      setReferenceImages((prev) => [...prev, ...previews]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取参考图失败";
      toast.error(message);
    }
  }, []);

  const handleReferenceImageChange = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }

      await appendReferenceImages(files);
    },
    [appendReferenceImages],
  );

  const handleRemoveReferenceImage = useCallback((index: number) => {
    setReferenceImageFiles((prev) => {
      const next = prev.filter((_, currentIndex) => currentIndex !== index);
      if (next.length === 0 && fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return next;
    });
    setReferenceImages((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
  }, []);

  const handleContinueEdit = useCallback(
    async (conversationId: string, image: StoredImage | StoredReferenceImage) => {
      try {
        const nextReference =
          "dataUrl" in image
            ? {
                referenceImage: image,
                file: dataUrlToFile(image.dataUrl, image.name, image.type),
              }
            : await buildReferenceImageFromStoredImage(image, `conversation-${conversationId}-${Date.now()}.png`);
        if (!nextReference) {
          return;
        }

        setSelectedConversationId(conversationId);

        setReferenceImages((prev) => [...prev, nextReference.referenceImage]);
        setReferenceImageFiles((prev) => [...prev, nextReference.file]);
        setImagePrompt("");
        textareaRef.current?.focus();
        toast.success("已加入当前参考图，继续输入描述即可编辑");
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取结果图失败";
        toast.error(message);
      }
    },
    [],
  );

  const handleReuseTurnConfig = useCallback(async (conversationId: string, turnId: string) => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    const turn = conversation?.turns.find((item) => item.id === turnId);
    if (!conversation || !turn || !turn.prompt.trim()) {
      return;
    }

    setSelectedConversationId(conversationId);
    setImagePrompt(turn.prompt);
    setImageModel(turn.model || FALLBACK_IMAGE_MODELS[0]);
    setImageCount(String(Math.max(1, turn.count || turn.images.length || 1)));
    setImageSize(turn.size);
    setReferenceImages(turn.referenceImages);
    setReferenceImageFiles(
      turn.referenceImages.map((image) => dataUrlToFile(image.dataUrl, image.name, image.type)),
    );
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    textareaRef.current?.focus();
    toast.success("已复用这条提示词配置");
  }, []);

  const openLightbox = useCallback((images: ImageLightboxItem[], index: number) => {
    if (images.length === 0) {
      return;
    }

    setLightboxImages(images);
    setLightboxIndex(Math.max(0, Math.min(index, images.length - 1)));
    setLightboxOpen(true);
  }, []);

  const createLoadingImages = (turnId: string, count: number) =>
    Array.from({ length: count }, (_, index) => {
      const imageId = `${turnId}-${index}`;
      return {
        id: imageId,
        taskId: imageId,
        status: "loading" as const,
      };
    });

  /* eslint-disable react-hooks/preserve-manual-memoization */
  const runConversationQueue = useCallback(
    async (conversationId: string) => {
      if (activeConversationQueueIds.has(conversationId)) {
        return;
      }

      const snapshot = conversationsRef.current.find((conversation) => conversation.id === conversationId);
      const activeTurn = snapshot?.turns.find(
        (turn) =>
          (turn.status === "queued" || turn.status === "generating") &&
          turn.images.some((image) => image.status === "loading"),
      );
      if (!snapshot || !activeTurn) {
        return;
      }

      activeConversationQueueIds.add(conversationId);
      const applyTasks = async (tasks: ImageTask[]) => {
        const taskMap = new Map(tasks.map((task) => [task.id, task]));
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          const turns = conversation.turns.map((turn) => {
            if (turn.id !== activeTurn.id) {
              return turn;
            }
            const images = turn.images.map((image) => {
              const taskId = image.taskId || image.id;
              const task = taskMap.get(taskId);
              return task ? taskDataToStoredImage({ ...image, taskId }, task) : image;
            });
            const derived = deriveTurnStatus({ ...turn, status: "generating", images });
            return {
              ...turn,
              ...derived,
              images,
            };
          });
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns,
          };
        });
      };

      try {
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns: conversation.turns.map((turn) =>
              turn.id === activeTurn.id
                ? {
                    ...turn,
                    status: "generating",
                    error: undefined,
                    images: turn.images.map((image) =>
                      image.status === "loading" ? { ...image, taskId: image.taskId || image.id } : image,
                    ),
                  }
                : turn,
            ),
          };
        });

        const referenceFiles = activeTurn.referenceImages.map((image, index) =>
          dataUrlToFile(image.dataUrl, image.name || `${activeTurn.id}-${index + 1}.png`, image.type),
        );
        if (activeTurn.mode === "edit" && referenceFiles.length === 0) {
          throw new Error("未找到可用于继续编辑的参考图");
        }

        const pendingImages = activeTurn.images.filter((image) => image.status === "loading");
        const submitted = await Promise.all(
          pendingImages.map((image) => {
            const taskId = image.taskId || image.id;
            return activeTurn.mode === "edit"
              ? createImageEditTask(taskId, referenceFiles, activeTurn.prompt, activeTurn.model, activeTurn.size)
              : createImageGenerationTask(taskId, activeTurn.prompt, activeTurn.model, activeTurn.size);
          }),
        );
        await applyTasks(submitted);

        while (true) {
          const latestConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
          const latestTurn = latestConversation?.turns.find((turn) => turn.id === activeTurn.id);
          const loadingTaskIds =
            latestTurn?.images.flatMap((image) =>
              image.status === "loading" && image.taskId ? [image.taskId] : [],
            ) || [];
          if (loadingTaskIds.length === 0) {
            break;
          }

          await sleep(2000);
          const taskList = await fetchImageTasks(loadingTaskIds);
          if (taskList.items.length > 0) {
            await applyTasks(taskList.items);
          }
          if (taskList.missing_ids.length > 0 && latestTurn) {
            const missingImages = latestTurn.images.filter(
              (image) => image.status === "loading" && image.taskId && taskList.missing_ids.includes(image.taskId),
            );
            const resubmitted = await Promise.all(
              missingImages.map((image) =>
                activeTurn.mode === "edit"
                  ? createImageEditTask(image.taskId || image.id, referenceFiles, activeTurn.prompt, activeTurn.model, activeTurn.size)
                  : createImageGenerationTask(image.taskId || image.id, activeTurn.prompt, activeTurn.model, activeTurn.size),
              ),
            );
            if (resubmitted.length > 0) {
              await applyTasks(resubmitted);
            }
          }
        }

        await loadQuota();
      } catch (error) {
        const message = error instanceof Error ? error.message : "生成图片失败";
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns: conversation.turns.map((turn) =>
              turn.id === activeTurn.id
                ? {
                    ...turn,
                    status: "error",
                    error: message,
                    images: turn.images.map((image) =>
                      image.status === "loading" ? { ...image, status: "error", error: message } : image,
                    ),
                  }
                : turn,
            ),
          };
        });
        toast.error(message);
      } finally {
        activeConversationQueueIds.delete(conversationId);
        for (const conversation of conversationsRef.current) {
          if (
            !activeConversationQueueIds.has(conversation.id) &&
            conversation.turns.some(
              (turn) =>
                (turn.status === "queued" || turn.status === "generating") &&
                turn.images.some((image) => image.status === "loading"),
            )
          ) {
            void runConversationQueue(conversation.id);
          }
        }
      }
    },
    [loadQuota, updateConversation],
  );
  /* eslint-enable react-hooks/preserve-manual-memoization */

  const handleRegenerateTurn = useCallback(
    async (conversationId: string, turnId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      const sourceTurn = conversation?.turns.find((turn) => turn.id === turnId);
      if (!conversation || !sourceTurn || !sourceTurn.prompt.trim()) {
        return;
      }

      const now = new Date().toISOString();
      const nextTurnId = createId();
      const count = Math.max(1, sourceTurn.count || sourceTurn.images.length || 1);
      const nextTurn: ImageTurn = {
        id: nextTurnId,
        prompt: sourceTurn.prompt,
        model: sourceTurn.model,
        mode: sourceTurn.mode,
        referenceImages: sourceTurn.referenceImages,
        count,
        size: sourceTurn.size,
        images: createLoadingImages(nextTurnId, count),
        createdAt: now,
        status: "queued",
      };
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: [...conversation.turns, nextTurn],
      };

      setSelectedConversationId(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId);
      toast.success("已加入重新生成队列");
    },
    [runConversationQueue],
  );

  const handleRetryImage = useCallback(
    async (conversationId: string, turnId: string, imageId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      if (!conversation) {
        return;
      }

      const now = new Date().toISOString();
      const retryImageId = `${turnId}-${createId()}`;
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: conversation.turns.map((turn) => {
          if (turn.id !== turnId) {
            return turn;
          }
          if (!turn.prompt.trim()) {
            return turn;
          }

          const images = turn.images.map((image) =>
            image.id === imageId
              ? {
                  id: retryImageId,
                  taskId: retryImageId,
                  status: "loading" as const,
                }
              : image,
          );
          const derived = deriveTurnStatus({ ...turn, status: "queued", images });
          return {
            ...turn,
            ...derived,
            images,
          };
        }),
      };

      setSelectedConversationId(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId);
    },
    [runConversationQueue],
  );

  useEffect(() => {
    for (const conversation of conversations) {
      if (
        !activeConversationQueueIds.has(conversation.id) &&
        conversation.turns.some(
          (turn) =>
            !turn.resultsDeleted &&
            (turn.status === "queued" || turn.status === "generating") &&
            turn.images.some((image) => image.status === "loading"),
        )
      ) {
        void runConversationQueue(conversation.id);
      }
    }
  }, [conversations, runConversationQueue]);

  const handleSubmit = async () => {
    const prompt = imagePrompt.trim();
    if (!prompt) {
      toast.error("请输入提示词");
      return;
    }

    const effectiveImageMode: ImageConversationMode = referenceImageFiles.length > 0 ? "edit" : "generate";

    const targetConversation = selectedConversationId
      ? conversationsRef.current.find((conversation) => conversation.id === selectedConversationId) ?? null
      : null;
    const now = new Date().toISOString();
    const conversationId = targetConversation?.id ?? createId();
    const turnId = createId();
    const draftTurn: ImageTurn = {
      id: turnId,
      prompt,
      model: imageModel,
      mode: effectiveImageMode,
      referenceImages: effectiveImageMode === "edit" ? referenceImages : [],
      count: parsedCount,
      size: imageSize,
      images: createLoadingImages(turnId, parsedCount),
      createdAt: now,
      status: "queued",
    };

    const baseConversation: ImageConversation = targetConversation
      ? {
          ...targetConversation,
          updatedAt: now,
          turns: [...targetConversation.turns, draftTurn],
        }
      : {
          id: conversationId,
          title: buildConversationTitle(prompt),
          createdAt: now,
          updatedAt: now,
          turns: [draftTurn],
        };

    setSelectedConversationId(conversationId);
    clearComposerInputs();

    await persistConversation(baseConversation);
    void runConversationQueue(conversationId);

    const targetStats = getImageConversationStats(baseConversation);
    if (targetStats.running > 0 || targetStats.queued > 1) {
      toast.success("已加入当前对话队列");
    } else if (!targetConversation) {
      toast.success("已创建新对话并开始处理");
    } else {
      toast.success("已发送到当前对话");
    }
  };

  const handleTextSubmit = async () => {
    const prompt = textPrompt.trim();
    if (!prompt) {
      toast.error("请输入文本试验内容");
      return;
    }

    setIsSubmittingText(true);
    const userMessage: TextChatMessage = {
      id: createId(),
      role: "user",
      content: prompt,
      model: textModel,
      createdAt: new Date().toISOString(),
    };
    setTextMessages((current) => [...current, userMessage]);
    setTextPrompt("");

    try {
      const apiMessages = textMessagesToChatMessages([...textMessages, userMessage]);
      const data = await createChatCompletion({
        model: textModel,
        messages: apiMessages,
        stream: false,
      });
      const content = data.choices[0]?.message?.content?.trim();
      if (!content) {
        throw new Error("模型未返回文本内容");
      }
      setTextMessages((current) => [
        ...current,
        {
          id: createId(),
          role: "assistant",
          content,
          model: data.model || textModel,
          createdAt: new Date().toISOString(),
        },
      ]);
      toast.success("文本试验完成");
    } catch (error) {
      const message = error instanceof Error ? error.message : "文本试验失败";
      setTextMessages((current) => [
        ...current,
        {
          id: createId(),
          role: "error",
          content: message,
          model: textModel,
          createdAt: new Date().toISOString(),
        },
      ]);
      toast.error(message);
    } finally {
      setIsSubmittingText(false);
    }
  };

  const handleClearTextMessages = async () => {
    try {
      await clearTextMessages();
      setTextMessages([]);
      toast.success("已清空文本聊天记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空文本聊天记录失败";
      toast.error(message);
    }
  };

  const updateTextModelTestResult = (modelId: string, update: Omit<TextModelTestResult, "model">) => {
    setTextModelTestResults((current) =>
      current.map((item) => (item.model === modelId ? { ...item, ...update } : item)),
    );
  };

  const handleBatchTextModelTest = async () => {
    const modelsToTest = uniqueModelIds(textModelOptions);
    if (modelsToTest.length === 0) {
      toast.error("暂无可测试的文本模型");
      return;
    }

    setIsTestingTextModels(true);
    setTextModelTestResults(
      modelsToTest.map((modelId) => ({
        model: modelId,
        status: "pending",
        message: "等待测试",
      })),
    );

    for (const modelId of modelsToTest) {
      updateTextModelTestResult(modelId, { status: "testing", message: "测试中" });
      try {
        const data = await createChatCompletion({
          model: modelId,
          messages: [{ role: "user", content: "Reply with OK." }],
          stream: false,
        });
        const content = data.choices[0]?.message?.content?.trim();
        if (!content) {
          throw new Error("模型未返回文本内容");
        }
        updateTextModelTestResult(modelId, { status: "success", message: "可用" });
      } catch (error) {
        const message = error instanceof Error ? error.message : "请求失败";
        updateTextModelTestResult(modelId, { status: "error", message });
      }
    }

    setIsTestingTextModels(false);
    toast.success("模型批量测试完成");
  };

  return (
    <>
      <section className="mx-auto flex h-[calc(100dvh-5.5rem)] min-h-0 w-full max-w-[1380px] flex-col gap-3 overflow-hidden px-0 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] sm:h-[calc(100dvh-5.25rem)] sm:px-3 sm:pb-5">
        <div className="rounded-[28px] border border-white/80 bg-white/58 px-4 py-3 shadow-[var(--shadow-soft)] backdrop-blur-xl sm:px-5">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">试验页面</div>
              <p className="mt-0.5 truncate text-sm text-stone-500">
                文生文走聊天接口，文生图保留队列和历史。
              </p>
            </div>
            <ExperimentModeSwitch mode={experimentMode} onChange={setExperimentMode} />
          </div>
        </div>

        {experimentMode === "text" ? (
          <TextExperimentPanel
            prompt={textPrompt}
            messages={textMessages}
            model={textModel}
            models={textModelOptions}
            isLoading={isSubmittingText}
            isTestingModels={isTestingTextModels}
            modelTestResults={textModelTestResults}
            onPromptChange={setTextPrompt}
            onModelChange={setTextModel}
            onSubmit={handleTextSubmit}
            onTestModels={handleBatchTextModelTest}
            onClearMessages={handleClearTextMessages}
          />
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-hidden sm:gap-3 lg:grid-cols-[240px_minmax(0,1fr)]">
            <div className="hidden h-full min-h-0 rounded-[24px] border border-white/70 bg-white/45 px-3 shadow-[var(--shadow-soft)] backdrop-blur-sm lg:block">
              <ImageSidebar
            conversations={conversations}
            isLoadingHistory={isLoadingHistory}
            selectedConversationId={selectedConversationId}
            onCreateDraft={handleCreateDraft}
            onClearHistory={openClearHistoryConfirm}
            onSelectConversation={setSelectedConversationId}
            onDeleteConversation={openDeleteConversationConfirm}
            onRenameConversation={handleRenameConversation}
            formatConversationTime={formatConversationTime}
              />
            </div>

            <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
          <DialogContent className="flex h-[min(82dvh,760px)] w-[92vw] max-w-[460px] flex-col overflow-hidden rounded-[32px] border-white/80 bg-white p-0 shadow-[0_32px_110px_-38px_rgba(15,23,42,0.45)] sm:rounded-[36px]">
            <DialogHeader className="px-6 pt-7 pb-4 sm:px-8">
              <DialogTitle className="flex items-center gap-2 text-xl font-bold tracking-tight">
                <History className="size-5" />
                历史记录
              </DialogTitle>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 sm:px-8">
              <ImageSidebar
                conversations={conversations}
                isLoadingHistory={isLoadingHistory}
                selectedConversationId={selectedConversationId}
                onCreateDraft={() => {
                  handleCreateDraft();
                  setIsHistoryOpen(false);
                }}
                onClearHistory={openClearHistoryConfirm}
                onSelectConversation={(id) => {
                  setSelectedConversationId(id);
                  setIsHistoryOpen(false);
                }}
                onDeleteConversation={openDeleteConversationConfirm}
                onRenameConversation={handleRenameConversation}
                formatConversationTime={formatConversationTime}
                hideActionButtons
              />
            </div>
          </DialogContent>
        </Dialog>

            <div className="flex min-h-0 flex-col gap-2 sm:gap-4">
          <div className="flex items-center justify-between gap-2 px-1 lg:hidden">
            <Button
              variant="outline"
              className="h-10 flex-1 rounded-2xl border-stone-200 bg-white/90 text-stone-700 shadow-sm"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History className="mr-2 size-4" />
              历史记录 ({conversations.length})
            </Button>
            <Button
              className="h-10 rounded-2xl bg-stone-950 text-white shadow-sm"
              onClick={handleCreateDraft}
            >
              <Plus className="size-4" />
              新建
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-2xl border-stone-200 bg-white/85 px-3 text-stone-600 shadow-sm"
              onClick={openClearHistoryConfirm}
              disabled={conversations.length === 0}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <div
            ref={resultsViewportRef}
            className="hide-scrollbar min-h-0 flex-1 overscroll-contain overflow-y-auto px-1 py-2 sm:px-4 sm:py-4"
          >
            <ImageResults
              selectedConversation={selectedConversation}
              onOpenLightbox={openLightbox}
              onContinueEdit={handleContinueEdit}
              onDeletePrompt={openDeletePromptConfirm}
              onDeleteResults={openDeleteResultsConfirm}
              onReuseTurnConfig={handleReuseTurnConfig}
              onRegenerateTurn={handleRegenerateTurn}
              onRetryImage={handleRetryImage}
              formatConversationTime={formatConversationTime}
            />
          </div>

          <ImageComposer
            prompt={imagePrompt}
            imageCount={imageCount}
            imageSize={imageSize}
            imageModel={imageModel}
            imageModels={imageModelOptions}
            availableQuota={availableQuota}
            activeTaskCount={activeTaskCount}
            referenceImages={referenceImages}
            textareaRef={textareaRef}
            fileInputRef={fileInputRef}
            onPromptChange={setImagePrompt}
            onImageCountChange={(value) => setImageCount(value ? clampImageCount(value) : "")}
            onImageSizeChange={setImageSize}
            onImageModelChange={setImageModel}
            onSubmit={handleSubmit}
            onPickReferenceImage={() => fileInputRef.current?.click()}
            onReferenceImageChange={handleReferenceImageChange}
            onRemoveReferenceImage={handleRemoveReferenceImage}
          />
            </div>
          </div>
        )}
      </section>

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />

      {deleteConfirm ? (
        <Dialog open onOpenChange={(open) => (!open ? setDeleteConfirm(null) : null)}>
          <DialogContent showCloseButton={false} className="rounded-2xl p-6">
            <DialogHeader className="gap-2">
              <DialogTitle>{deleteConfirmTitle}</DialogTitle>
              <DialogDescription className="text-sm leading-6">
                {deleteConfirmDescription}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                取消
              </Button>
              <Button className="bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleConfirmDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

export default function ImagePage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <ImagePageContent isAdmin={session.role === "admin"} />;
}
