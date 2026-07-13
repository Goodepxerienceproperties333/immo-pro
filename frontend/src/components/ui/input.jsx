import * as React from "react"

import { cn } from "@/lib/utils"

// iter90er : bloque la modification des montants a la roulette souris
// (comportement natif tres intrusif sur les inputs number). On blur
// l'input focus sur wheel : la valeur reste intacte et la page scroll
// normalement.
const handleWheelNumber = (e) => {
  if (document.activeElement === e.currentTarget) {
    e.currentTarget.blur();
  }
};

const Input = React.forwardRef(({ className, type, onWheel, ...props }, ref) => {
  const wheelHandler = type === "number"
    ? (e) => { handleWheelNumber(e); if (onWheel) onWheel(e); }
    : onWheel;
  return (
    <input
      type={type}
      className={cn(
        // iter90bq : radius doux + focus ring elegant + bg blanc
        "flex h-9 w-full rounded-lg border border-slate-200 bg-white px-3 py-1 text-base shadow-sm transition-all file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-slate-400 focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className
      )}
      ref={ref}
      onWheel={wheelHandler}
      {...props} />
  );
})
Input.displayName = "Input"

export { Input }
