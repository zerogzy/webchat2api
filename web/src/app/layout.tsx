import type { Metadata, Viewport } from "next";
import { Toaster } from "sonner";
import "./globals.css";
import { TopNav } from "@/components/top-nav";

export const metadata: Metadata = {
  title: "webchat2api 管理后台",
  description: "webchat2api management dashboard",
  icons: {
    icon: [
      { url: "/favicon.ico" },
      { url: "/webchat2api-logo.png", type: "image/png" },
    ],
    apple: [{ url: "/webchat2api-logo.png", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: "#f0ebe3",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body
        className="antialiased"
        style={{
          fontFamily:
            '"SF Pro Display","SF Pro Text","PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif',
        }}
      >
        <Toaster position="top-center" richColors offset={48} />
        <main className="relative min-h-screen overflow-x-hidden bg-[radial-gradient(circle_at_12%_0%,_rgba(255,252,246,0.86),_transparent_30rem),radial-gradient(circle_at_88%_12%,_rgba(224,232,207,0.46),_transparent_28rem),linear-gradient(135deg,_rgba(250,246,238,0.98),_rgba(241,234,222,0.96)_52%,_rgba(246,242,232,0.98))] px-4 pt-0 pb-3 text-stone-900 sm:px-6 sm:pt-3 lg:px-8">
          <div className="pointer-events-none fixed inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-stone-300/70 to-transparent" />
          <div className="mx-auto box-border flex min-h-screen max-w-[1440px] flex-col gap-3 pt-[env(safe-area-inset-top)] sm:gap-6 sm:pt-0">
            <TopNav />
            {children}
          </div>
        </main>
      </body>
    </html>
  );
}
