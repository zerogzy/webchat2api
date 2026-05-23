"use client";

export function SettingsHeader() {
  return (
    <section className="rounded-[28px] border border-white/70 bg-white/50 p-5 shadow-[var(--shadow-soft)] backdrop-blur-sm lg:p-6">
      <div className="space-y-1">
        <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Settings</div>
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
      </div>
    </section>
  );
}
