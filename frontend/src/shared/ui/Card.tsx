import type { ReactNode } from "react";

export interface CardProps {
  children: ReactNode;
  className?: string;
}

/** Generic content container: white surface, hairline border, consistent padding/radius. No
 *  domain knowledge -- `StatCard` and other composed pieces build on top of this. */
export function Card({ children, className = "" }: CardProps) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm ${className}`}>
      {children}
    </div>
  );
}
