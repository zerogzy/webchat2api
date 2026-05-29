"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LoaderCircle, LockKeyhole } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { login } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function LoginPage() {
  const router = useRouter();
  const [authKey, setAuthKey] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { isCheckingAuth } = useRedirectIfAuthenticated();

  const handleLogin = async () => {
    const normalizedAuthKey = authKey.trim();
    if (!normalizedAuthKey) {
      toast.error("请输入 密钥");
      return;
    }

    setIsSubmitting(true);
    try {
      const data = await login(normalizedAuthKey);
      await setStoredAuthSession({
        key: normalizedAuthKey,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
      });
      router.replace(getDefaultRouteForRole(data.role));
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-4 py-6">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-2 py-6 sm:px-4">
      <div className="absolute inset-x-4 top-10 hidden h-32 rounded-full bg-[radial-gradient(circle,var(--surface-amber),transparent_70%)] opacity-50 blur-3xl sm:block" />
      <Card className="relative w-full max-w-[505px] overflow-hidden rounded-[34px] border-white/80 bg-card/88 shadow-[var(--shadow-lift)]">
        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-secondary via-[var(--surface-amber)] to-accent" />
        <CardContent className="space-y-8 p-6 sm:p-8">
          <div className="space-y-4 text-center">
            <div className="mx-auto inline-flex size-14 items-center justify-center rounded-[22px] border border-white/75 bg-[linear-gradient(145deg,var(--primary),var(--chart-5))] text-primary-foreground shadow-[0_18px_42px_-26px_oklch(0.22_0.024_65_/_0.95)]">
              <LockKeyhole className="size-5" />
            </div>
            <div className="space-y-2">
              <p className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">Management Console</p>
              <h1 className="text-3xl font-semibold tracking-tight text-foreground">欢迎回来</h1>
              <p className="mx-auto max-w-sm text-sm leading-6 text-muted-foreground">输入密钥后继续进入温暖的玻璃控制台，管理账号、图片生成与系统配置。</p>
            </div>
          </div>

          <div className="space-y-3">
            <label htmlFor="auth-key" className="block text-sm font-medium text-foreground">
              密钥
            </label>
            <Input
              id="auth-key"
              type="password"
              value={authKey}
              onChange={(event) => setAuthKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void handleLogin();
                }
              }}
              placeholder="请输入密钥"
              className="h-13 rounded-2xl border-white/80 bg-card/82 px-4 shadow-inner shadow-stone-200/30 focus-visible:ring-ring/45"
            />
          </div>

          <Button
            className="h-13 w-full rounded-2xl shadow-[0_18px_42px_-28px_oklch(0.22_0.024_65_/_0.9)]"
            onClick={() => void handleLogin()}
            disabled={isSubmitting}
          >
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            登录
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
