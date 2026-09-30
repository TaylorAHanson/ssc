import { useEffect, useState } from 'react';
import { Check, Link2 } from 'lucide-react';

interface CopyLinkButtonProps {
  /** Absolute URL to copy. */
  url: string;
  className?: string;
  /** Classes for the text label (e.g. to hide it on narrow layouts). */
  labelClassName?: string;
  iconClassName?: string;
  title?: string;
}

export function CopyLinkButton({
  url,
  className = '',
  labelClassName = '',
  iconClassName = 'w-3.5 h-3.5',
  title = 'Copy link',
}: CopyLinkButtonProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(id);
  }, [copied]);

  const handleCopy = async () => {
    if (!navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      /* clipboard blocked — silently no-op */
    }
  };

  const Icon = copied ? Check : Link2;
  return (
    <button type="button" onClick={handleCopy} className={className} title={title} aria-label={title}>
      <Icon className={`${iconClassName} ${copied ? 'text-emerald-600' : ''}`} />
      <span className={labelClassName}>{copied ? 'Copied' : 'Copy link'}</span>
    </button>
  );
}
