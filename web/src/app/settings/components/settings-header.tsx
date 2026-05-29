"use client";

export function SettingsHeader() {
  return (
    <section className="relative overflow-hidden rounded-[32px] border border-white/75 bg-card/82 p-5 shadow-[var(--shadow-lift)] backdrop-blur-xl sm:p-6 lg:p-7">
      <div className="absolute -top-24 right-8 h-40 w-40 rounded-full bg-[radial-gradient(circle,var(--surface-sage),transparent_70%)] opacity-55 blur-2xl" />
      <div className="absolute -bottom-28 left-10 h-44 w-44 rounded-full bg-[radial-gradient(circle,var(--surface-amber),transparent_70%)] opacity-45 blur-3xl" />
      <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl space-y-3">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/75 bg-card/70 px-3 py-1 text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase shadow-sm">
            <span className="size-1.5 rounded-full bg-accent" />
            Settings Control Room
          </div>
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">设置控制台</h1>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
              集中校准系统配置、备份策略、用户密钥与外部连接。先确认基础参数，再维护导入和同步通道。
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:flex sm:flex-wrap sm:justify-end">
          <div className="rounded-2xl border border-white/70 bg-card/68 px-3 py-2 shadow-sm">
            <div className="font-semibold text-foreground">Config</div>
            <div>核心参数</div>
          </div>
          <div className="rounded-2xl border border-white/70 bg-card/68 px-3 py-2 shadow-sm">
            <div className="font-semibold text-foreground">Access</div>
            <div>密钥与权限</div>
          </div>
          <div className="rounded-2xl border border-white/70 bg-card/68 px-3 py-2 shadow-sm">
            <div className="font-semibold text-foreground">Links</div>
            <div>远程连接</div>
          </div>
        </div>
      </div>
    </section>
  );
}
