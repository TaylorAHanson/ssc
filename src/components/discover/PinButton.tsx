import { useEffect, useState } from 'react';
import type { MouseEvent } from 'react';
import { Pin, PinOff } from 'lucide-react';
import type { PinnedAssetInput } from '../../services/api';
import { usePinnedAssets } from '../../lib/usePinnedAssets';

interface PinButtonProps {
  asset: PinnedAssetInput;
  /**
   * `icon`: small corner toggle for cards — appears on hover of the nearest
   * `group` ancestor and stays visible once pinned.
   * `labeled`: "Pin" / "Pinned" button for detail headers.
   */
  variant?: 'icon' | 'labeled';
  className?: string;
}

/** Self-contained pin toggle backed by the shared pinned-assets store. */
export function PinButton({ asset, variant = 'icon', className = '' }: PinButtonProps) {
  const { isPinned, togglePin } = usePinnedAssets();
  const pinned = isPinned(asset.asset_id);
  const [failure, setFailure] = useState<string | null>(null);
  const [hovering, setHovering] = useState(false);

  useEffect(() => {
    if (!failure) return;
    const t = setTimeout(() => setFailure(null), 4000);
    return () => clearTimeout(t);
  }, [failure]);

  const onClick = (e: MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setFailure(null);
    togglePin(asset).catch((err: unknown) => setFailure(err instanceof Error ? err.message : 'Something went wrong'));
  };

  const title = failure ?? (pinned ? 'Unpin from your pinned assets' : 'Pin for quick access from Discover');

  if (variant === 'labeled') {
    const Icon = pinned && hovering ? PinOff : Pin;
    return (
      <button
        type="button"
        onClick={onClick}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        aria-pressed={pinned}
        title={title}
        className={`inline-flex items-center gap-1 text-xs font-medium cursor-pointer transition-colors ${
          failure ? 'text-rose-600' : pinned ? 'text-primary' : 'text-slate-500 hover:text-primary'
        } ${className}`}
      >
        <Icon className={`w-3.5 h-3.5 ${pinned && !hovering ? 'fill-current' : ''}`} />
        {failure ? "Couldn't update pin" : pinned ? 'Pinned' : 'Pin'}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pinned}
      aria-label={pinned ? 'Unpin asset' : 'Pin asset'}
      title={title}
      className={`p-1 rounded transition-all cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${
        failure
          ? 'opacity-100 text-rose-600'
          : pinned
            ? 'opacity-100 text-primary hover:bg-primary/10'
            : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 text-slate-400 hover:text-slate-600 hover:bg-slate-100'
      } ${className}`}
    >
      <Pin className={`w-3.5 h-3.5 ${pinned ? 'fill-current' : ''}`} />
    </button>
  );
}
