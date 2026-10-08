import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Teach tailwind-merge the custom type scale (globals.css @theme) so `text-body` is treated as a font size,
// not a colour — otherwise it would strip real colour classes like `text-background`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["meta", "small", "body", "lead", "section", "title"] }],
      rounded: [{ rounded: ["control", "panel"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
