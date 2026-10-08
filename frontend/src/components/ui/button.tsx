import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { cn } from "@/utils/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control text-small font-medium transition-colors duration-[var(--dur-fast)] disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-strong text-background hover:bg-foreground",
        secondary: "border border-border-strong bg-raised text-foreground hover:bg-hover hover:border-[#3a3a3a]",
        ghost: "text-muted hover:bg-hover hover:text-foreground",
        danger: "bg-danger text-white hover:bg-[#d2443d]",
        "danger-outline": "border border-danger/60 text-danger hover:bg-danger/10",
        success: "bg-success text-background hover:bg-[#5cbf8c]",
        link: "text-foreground underline underline-offset-4 hover:text-strong px-0",
      },
      size: { sm: "h-7 px-2.5 text-meta", md: "h-8 px-3", lg: "h-10 px-4 text-body", icon: "h-8 w-8", "icon-sm": "h-7 w-7" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, loading, disabled, children, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading}
            aria-busy={loading || undefined} {...props}>
        {asChild ? children : (<>{loading && <Loader2 className="animate-spin" aria-hidden />}{children}</>)}
      </Comp>
    );
  },
);
Button.displayName = "Button";
export { buttonVariants };
