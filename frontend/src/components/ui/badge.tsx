import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/utils/cn";

const badgeVariants = cva("inline-flex items-center gap-1.5 rounded-[4px] border px-1.5 py-px text-meta font-medium leading-5 whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "border-border-strong bg-raised text-muted",
      strong: "border-border-strong bg-raised text-foreground",
      success: "border-success/30 bg-success/10 text-success",
      warning: "border-warning/30 bg-warning/10 text-warning",
      danger: "border-danger/35 bg-danger/10 text-danger",
      info: "border-info/30 bg-info/10 text-info",
      voice: "border-voice/30 bg-voice/10 text-voice",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
