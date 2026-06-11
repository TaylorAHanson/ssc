import { useState, type ReactNode } from 'react';
import { HelpCircle, MessageCircleQuestion } from 'lucide-react';
import { Link } from 'react-router-dom';

/**
 * A small question-mark icon that reveals a hint on hover/focus/click.
 * Pure CSS/state, no external dependency. Use next to form labels to explain
 * a field without sending the user off to the full guide.
 */
export function HelpTip({ text, className = '' }: { text: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className={`relative inline-flex align-middle ${className}`}>
      <button
        type="button"
        aria-label="Help"
        className="text-gray-400 hover:text-accent focus:text-accent focus:outline-none"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <HelpCircle className="w-3.5 h-3.5" />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute z-50 left-1/2 -translate-x-1/2 bottom-full mb-1.5 w-64 rounded-md bg-gray-900 text-white text-[11px] font-normal leading-snug px-2.5 py-1.5 shadow-lg pointer-events-none"
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** A form label with a trailing help tooltip. */
export function LabelWithHelp({
  children,
  help,
  className = '',
}: {
  children: ReactNode;
  help: ReactNode;
  className?: string;
}) {
  return (
    <label className={`flex items-center gap-1 ${className}`}>
      <span>{children}</span>
      <HelpTip text={help} />
    </label>
  );
}

/**
 * "Have questions? Ask the agent" affordance. When `onClick` is provided it
 * renders a button (used to open an in-page assistant panel so the user can see
 * the agent and the editor at once). Otherwise it links to the unified chat in a
 * new tab. Admins have the workflow-authoring tools in chat, so the assistant
 * can co-author with them.
 */
export function AskAgentHint({
  className = '',
  onClick,
  label = 'Have questions? Ask the agent',
}: {
  className?: string;
  onClick?: () => void;
  label?: string;
}) {
  const cls = `inline-flex items-center gap-1.5 text-xs text-accent hover:underline ${className}`;
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={cls}>
        <MessageCircleQuestion className="w-3.5 h-3.5" />
        {label}
      </button>
    );
  }
  return (
    <Link to="/" target="_blank" rel="noopener noreferrer" className={cls}>
      <MessageCircleQuestion className="w-3.5 h-3.5" />
      {label}
    </Link>
  );
}

export default HelpTip;
