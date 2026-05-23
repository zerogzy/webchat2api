import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "border-input placeholder:text-muted-foreground selection:bg-accent selection:text-accent-foreground flex h-11 w-full min-w-0 rounded-2xl border bg-white/80 px-4 py-2 text-sm shadow-sm transition-[border-color,background-color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-ring focus-visible:bg-white focus-visible:ring-[3px] focus-visible:ring-ring/25",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
