import { HelpCircle } from 'lucide-react';
import type { ReactNode } from 'react';

interface InfoTipProps {
  text: ReactNode;
  /** Tailwind alignment of the popover relative to the icon. Defaults to centered. */
  align?: 'center' | 'left' | 'right';
  className?: string;
}

/**
 * A small, dependency-free help affordance: a visible "?" icon that reveals a
 * tooltip on hover/focus. Used instead of the native `title` attribute, which is
 * slow to appear and easy to miss. Keyboard-accessible via `tabIndex`.
 */
export function InfoTip({ text, align = 'center', className = '' }: InfoTipProps) {
  const alignClasses =
    align === 'left'
      ? 'left-0'
      : align === 'right'
        ? 'right-0'
        : 'left-1/2 -translate-x-1/2';

  return (
    <span className={`group/infotip relative inline-flex items-center align-middle ${className}`}>
      <HelpCircle
        tabIndex={0}
        className="w-3.5 h-3.5 text-gray-400 hover:text-gray-600 focus:text-gray-600 outline-none cursor-help"
      />
      <span
        role="tooltip"
        className={`pointer-events-none absolute top-full z-50 mt-1.5 hidden w-64 whitespace-normal break-words rounded-md bg-gray-900 px-2.5 py-1.5 text-xs font-normal leading-snug text-white shadow-lg group-hover/infotip:block group-focus-within/infotip:block ${alignClasses}`}
      >
        {text}
      </span>
    </span>
  );
}

export default InfoTip;
