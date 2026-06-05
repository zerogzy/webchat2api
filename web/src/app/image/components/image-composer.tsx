"use client";

import {
  ArrowUp,
  ImagePlus,
  LoaderCircle,
  X,
  Minimize2,
  Maximize2,
} from "lucide-react";
import {
  useMemo,
  useState,
  type ClipboardEvent,
  type RefObject,
} from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageSize: string;
  imageModel: string;
  imageModels: string[];
  imageProvider: string;
  imageProviderOptions: Array<{
    value: string;
    label: string;
    disabled?: boolean;
    reason?: string;
  }>;
  imageModeUnavailableMessage?: string;
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onImageModelChange: (value: string) => void;
  onImageProviderChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
};

export function ImageComposer({
  prompt,
  imageCount,
  imageSize,
  imageModel,
  imageModels,
  imageProvider,
  imageProviderOptions,
  imageModeUnavailableMessage,
  availableQuota,
  activeTaskCount,
  referenceImages,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onImageModelChange,
  onImageProviderChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const lightboxImages = useMemo(
    () =>
      referenceImages.map((image, index) => ({
        id: `${image.name}-${index}`,
        src: image.dataUrl,
      })),
    [referenceImages],
  );
  const hasSelectableImageModel =
    imageModels.length > 0 && imageModels.includes(imageModel);
  const imageModelPlaceholder =
    imageModels.length === 0 ? "暂无可用模型" : "请选择模型";
  const imageSizeOptions = [
    { value: "__unset__", label: "未指定" },
    { value: "1:1", label: "1:1 (正方形)" },
    { value: "16:9", label: "16:9 (横版)" },
    { value: "4:3", label: "4:3 (横版)" },
    { value: "3:4", label: "3:4 (竖版)" },
    { value: "9:16", label: "9:16 (竖版)" },
  ];

  const handleTextareaPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (imageFiles.length === 0) {
      return;
    }

    event.preventDefault();
    void onReferenceImageChange(imageFiles);
  };

  return (
    <div className="shrink-0 px-1 sm:px-0">
      <div className="mx-auto w-full max-w-[980px]">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => {
            void onReferenceImageChange(Array.from(event.target.files || []));
          }}
        />

        {imageModeUnavailableMessage && !isCollapsed ? (
          <p className="mb-2 rounded-xl border border-amber-200/80 bg-amber-50/90 px-3 py-2 text-xs leading-5 text-amber-700 sm:mb-3">
            {imageModeUnavailableMessage}
          </p>
        ) : null}

        {referenceImages.length > 0 && !isCollapsed ? (
          <div className="mb-2 border-b border-stone-200/80 pb-2 sm:mb-3 sm:pb-3">
            <div className="mb-2 flex items-center justify-between gap-2 px-1">
              <span className="text-[11px] font-semibold tracking-[0.14em] text-stone-500 uppercase">
                参考图
              </span>
              <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[11px] font-medium text-stone-500">
                {referenceImages.length} 张
              </span>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
              {referenceImages.map((image, index) => (
                <div
                  key={`${image.name}-${index}`}
                  className="relative size-14 shrink-0 sm:size-16"
                >
                  <button
                    type="button"
                    onClick={() => {
                      setLightboxIndex(index);
                      setLightboxOpen(true);
                    }}
                    className="group size-14 overflow-hidden rounded-2xl border border-stone-200 bg-stone-50 transition hover:border-stone-300 sm:size-16"
                    aria-label={`预览参考图 ${image.name || index + 1}`}
                  >
                    <img
                      src={image.dataUrl}
                      alt={image.name || `参考图 ${index + 1}`}
                      className="h-full w-full object-cover"
                    />
                  </button>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onRemoveReferenceImage(index);
                    }}
                    className="absolute -right-1 -top-1 inline-flex size-5 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-800"
                    aria-label={`移除参考图 ${image.name || index + 1}`}
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div
          className={`relative overflow-hidden rounded-2xl border border-stone-200/80 bg-white/72 transition focus-within:border-stone-300 ${
            isCollapsed
              ? "cursor-pointer px-3 py-2 hover:bg-white sm:px-4 sm:py-3"
              : ""
          }`}
        >
          <div
            className={`relative ${
              isCollapsed ? "flex items-center justify-between" : "cursor-text"
            }`}
            onClick={() => {
              if (isCollapsed) {
                setIsCollapsed(false);
                setTimeout(() => textareaRef.current?.focus(), 0);
              } else {
                textareaRef.current?.focus();
              }
            }}
          >
            {isCollapsed ? (
              <div className="flex w-full items-center justify-between gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="shrink-0 rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-semibold text-stone-600 sm:text-xs">
                    {referenceImages.length > 0 ? "编辑模式" : "生成模式"}
                  </span>
                  <p className="truncate text-sm font-medium text-stone-600 sm:text-[15px]">
                    {prompt || "输入想要生成的画面..."}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      setIsCollapsed(false);
                      setTimeout(() => textareaRef.current?.focus(), 0);
                    }}
                    className="inline-flex size-8 items-center justify-center rounded-full text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-700"
                    aria-label="展开输入框"
                  >
                    <Maximize2 className="size-4" />
                  </button>
                </div>
              </div>
            ) : (
              <>
            <ImageLightbox
              images={lightboxImages}
              currentIndex={lightboxIndex}
              open={lightboxOpen}
              onOpenChange={setLightboxOpen}
              onIndexChange={setLightboxIndex}
            />
            <Textarea
              ref={textareaRef}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onPaste={handleTextareaPaste}
              placeholder={
                referenceImages.length > 0
                  ? "描述你希望如何修改参考图"
                  : "输入你想要生成的画面，也可直接粘贴图片"
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void onSubmit();
                }
              }}
              className="min-h-[160px] resize-none rounded-none border-0 bg-transparent px-4 pt-4 pb-4 text-[15px] leading-6 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:ring-0 sm:min-h-[180px] sm:px-5 sm:pt-5 sm:pb-5 sm:leading-7"
            />

            <div
              className="border-t border-stone-200/70 bg-stone-50/70 px-3 py-3 sm:px-4 sm:py-4"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-2 hidden items-center justify-between gap-2 text-[11px] font-semibold tracking-[0.14em] text-stone-500 uppercase sm:flex">
                <span>生成设置</span>
                <span>
                  {referenceImages.length > 0 ? "编辑模式" : "生成模式"}
                </span>
              </div>
              <div className="flex items-end justify-between gap-2 sm:gap-3">
                <div className="hide-scrollbar flex min-w-0 flex-1 flex-nowrap items-center gap-1.5 overflow-x-auto pb-0.5 sm:flex-wrap sm:gap-3 sm:overflow-visible sm:pb-0">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 shrink-0 rounded-full border-stone-200 bg-white/90 px-3 text-xs font-medium text-stone-700 shadow-sm sm:h-10 sm:px-4 sm:text-sm"
                    onClick={onPickReferenceImage}
                    aria-label={
                      referenceImages.length > 0 ? "添加参考图" : "上传"
                    }
                  >
                    <ImagePlus className="size-3.5 sm:size-4" />
                    <span className="hidden sm:inline">
                      {referenceImages.length > 0 ? "添加参考图" : "上传"}
                    </span>
                  </Button>
                  <div className="shrink-0 rounded-full border border-amber-200/70 bg-amber-50/80 px-2 py-1 text-[10px] font-medium text-amber-800 sm:px-3 sm:py-2 sm:text-xs">
                    <span className="hidden sm:inline">剩余额度 </span>
                    {availableQuota}
                  </div>
                  {activeTaskCount > 0 && (
                    <div className="flex shrink-0 items-center gap-1 rounded-full border border-lime-200/70 bg-lime-50/85 px-2 py-1 text-[10px] font-medium text-lime-800 sm:gap-1.5 sm:px-3 sm:py-2 sm:text-xs">
                      <LoaderCircle className="size-3 animate-spin" />
                      {activeTaskCount}
                      <span className="hidden sm:inline"> 个任务处理中</span>
                    </div>
                  )}
                  <div className="flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white/90 px-2 py-0.5 shadow-sm sm:h-auto sm:gap-2 sm:px-3 sm:py-1">
                    <span className="hidden text-[11px] font-medium text-stone-700 sm:inline sm:text-sm">
                      张数
                    </span>
                    <Input
                      type="number"
                      inputMode="numeric"
                      min="1"
                      max="100"
                      step="1"
                      value={imageCount}
                      onChange={(event) =>
                        onImageCountChange(event.target.value)
                      }
                      className="h-7 w-[40px] border-0 bg-transparent px-0 text-center text-xs font-medium text-stone-700 shadow-none focus-visible:ring-0 sm:h-8 sm:w-[64px] sm:text-sm"
                    />
                  </div>
                  <div className="relative flex h-9 min-w-[150px] shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2 py-0.5 text-[11px] sm:h-auto sm:min-w-[150px] sm:gap-2 sm:px-3 sm:py-1 sm:text-[13px]">
                    <span className="hidden font-medium text-stone-700 sm:inline sm:text-sm">
                      比例
                    </span>
                    <Select
                      value={imageSize === "" ? "__unset__" : imageSize}
                      onValueChange={(val) =>
                        onImageSizeChange(val === "__unset__" ? "" : val)
                      }
                    >
                      <SelectTrigger
                        aria-label="图片比例"
                        className="h-8 min-w-0 flex-1 rounded-full border-0 bg-transparent px-0 text-xs font-bold text-stone-700 shadow-none focus:ring-0 sm:text-sm"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {imageSizeOptions.map((option) => (
                          <SelectItem
                            key={option.value}
                            value={option.value}
                          >
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex h-9 min-w-[150px] shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2 py-0.5 sm:h-auto sm:min-w-[178px] sm:gap-2 sm:px-3 sm:py-1">
                    <span className="hidden text-[11px] font-medium text-stone-700 sm:inline sm:text-sm">
                      服务
                    </span>
                    <Select
                      value={imageProvider}
                      onValueChange={onImageProviderChange}
                    >
                      <SelectTrigger className="h-8 min-w-0 flex-1 rounded-full border-0 bg-transparent px-0 text-xs font-bold text-stone-700 shadow-none focus:ring-0 sm:text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {imageProviderOptions.map((option) => (
                          <SelectItem
                            key={option.value}
                            value={option.value}
                            disabled={option.disabled}
                          >
                            <span>{option.label}</span>
                            {option.reason ? (
                              <span className="ml-2 text-xs text-stone-400">
                                {option.reason}
                              </span>
                            ) : null}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex h-9 min-w-[168px] shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2 py-0.5 sm:h-auto sm:min-w-[220px] sm:gap-2 sm:px-3 sm:py-1">
                    <span className="hidden text-[11px] font-medium text-stone-700 sm:inline sm:text-sm">
                      模型
                    </span>
                    {hasSelectableImageModel ? (
                      <Select
                        value={imageModel}
                        onValueChange={onImageModelChange}
                      >
                        <SelectTrigger className="h-8 min-w-0 flex-1 rounded-full border-0 bg-transparent px-0 text-xs font-bold text-stone-700 shadow-none focus:ring-0 sm:text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {imageModels.map((model) => (
                            <SelectItem key={model} value={model}>
                              {model}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <button
                        type="button"
                        disabled
                        className="h-8 min-w-0 flex-1 rounded-full border-0 bg-transparent px-0 text-left text-xs font-bold text-stone-400 shadow-none disabled:cursor-not-allowed sm:text-sm"
                        aria-label={
                          imageModels.length === 0
                            ? "当前服务暂无可用图像模型"
                            : "请选择图像模型"
                        }
                      >
                        {imageModelPlaceholder}
                      </button>
                    )}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => void onSubmit()}
                  disabled={
                    !prompt.trim() || Boolean(imageModeUnavailableMessage)
                  }
                  className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-stone-950 text-white shadow-[0_18px_38px_-24px_rgba(68,64,60,0.95)] transition hover:bg-stone-800 hover:shadow-[0_22px_44px_-24px_rgba(68,64,60,0.95)] disabled:cursor-not-allowed disabled:bg-stone-300 disabled:shadow-none sm:size-12"
                  aria-label={
                    referenceImages.length > 0 ? "编辑图片" : "生成图片"
                  }
                >
                  <ArrowUp className="size-3.5 sm:size-4" />
                </button>
              </div>
            </div>
            </>
            )}
          </div>
          {!isCollapsed && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setIsCollapsed(true);
              }}
              className="absolute top-3 right-3 z-10 inline-flex size-8 items-center justify-center rounded-full text-stone-400 transition-colors hover:bg-stone-100 hover:text-stone-700 sm:top-4 sm:right-4"
              aria-label="收起输入框"
            >
              <Minimize2 className="size-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
