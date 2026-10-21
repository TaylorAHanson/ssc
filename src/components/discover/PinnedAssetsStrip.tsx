import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowLeftRight, Pin, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { PinnedAsset } from '../../services/api';
import { getAssetTypeMeta } from '../../lib/assetTypes';
import { useLegacyMappings } from '../../lib/catalogCache';
import { LEGACY_DASHBOARD_PIN_TYPE, usePinnedAssets } from '../../lib/usePinnedAssets';

const GAP_PX = 6; // gap-1.5

function iconFor(pin: PinnedAsset): { Icon: LucideIcon; tone: string } {
  if (pin.asset_type === LEGACY_DASHBOARD_PIN_TYPE) return { Icon: ArrowLeftRight, tone: 'text-slate-500' };
  const meta = getAssetTypeMeta(pin.asset_type);
  return { Icon: meta.icon, tone: meta.accentText };
}

function tooltipFor(pin: PinnedAsset): string {
  const lines = [pin.title];
  if (pin.subtitle) lines.push(pin.subtitle);
  if (pin.available === false) lines.push('No longer in the catalog');
  return lines.join('\n');
}

const PILL =
  'inline-flex items-center shrink-0 max-w-[11rem] h-6 rounded-full border border-slate-200 bg-white text-xs text-slate-700';

/** Pill visuals, without handlers — shared by the real pills and the measuring mirror. */
function PillBody({ pin }: { pin: PinnedAsset }) {
  const { Icon, tone } = iconFor(pin);
  return (
    <>
      <Icon className={`w-3 h-3 shrink-0 ${tone}`} />
      <span className={`truncate ${pin.available === false ? 'line-through text-slate-400' : ''}`}>{pin.title}</span>
    </>
  );
}

interface PinnedAssetsStripProps {
  /** Open an asset in Discover (the page's `?asset=` panel). */
  onOpen: (assetId: string) => void;
}

/**
 * One-line strip of the user's pinned Discover assets for the top of the
 * landing page. Fits as many pills as the width allows and folds the rest into
 * a "+N" popover, so it never wraps. Renders nothing when there are no pins.
 */
export function PinnedAssetsStrip({ onOpen }: PinnedAssetsStripProps) {
  const { pins, unpin } = usePinnedAssets();
  const { data: legacyMappings } = useLegacyMappings();

  const rowRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const plusMirrorRef = useRef<HTMLSpanElement>(null);
  const [fit, setFit] = useState(pins.length);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuAlignRight, setMenuAlignRight] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const measure = useCallback(() => {
    const row = rowRef.current;
    const mirror = mirrorRef.current;
    if (!row || !mirror) return;
    const available = row.clientWidth;
    const widths = Array.from(mirror.children).map((el) => (el as HTMLElement).offsetWidth);
    const plus = plusMirrorRef.current?.offsetWidth ?? 32;
    const total = (k: number) => widths.slice(0, k).reduce((s, w) => s + w, 0) + GAP_PX * Math.max(0, k - 1);
    let next = widths.length;
    if (total(next) > available) {
      next = 0;
      while (next < widths.length && total(next + 1) + GAP_PX + plus <= available) next += 1;
    }
    setFit((prev) => (prev === next ? prev : next));
  }, []);

  const pinsKey = pins.map((p) => `${p.asset_id}\u0000${p.title}\u0000${p.available}`).join('\u0001');
  useLayoutEffect(() => {
    measure();
  }, [measure, pinsKey]);

  // The row only exists once there are pins, so re-attach when that flips.
  // The mirror is observed too, so late-loading fonts re-trigger a measure.
  const hasPins = pins.length > 0;
  useEffect(() => {
    const row = rowRef.current;
    const mirror = mirrorRef.current;
    if (!row || !mirror || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(row);
    ro.observe(mirror);
    return () => ro.disconnect();
  }, [measure, hasPins]);

  const hidden = pins.slice(fit);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  if (pins.length === 0) return null;

  const open = (pin: PinnedAsset) => {
    setMenuOpen(false);
    if (pin.asset_type === LEGACY_DASHBOARD_PIN_TYPE) {
      const mapping = legacyMappings.find((m) => m.id === pin.asset_id);
      if (mapping) return onOpen(mapping.metric_view_id);
      if (pin.url) {
        window.open(pin.url, '_blank', 'noopener,noreferrer');
        return;
      }
    }
    onOpen(pin.asset_id);
  };

  const remove = (pin: PinnedAsset) => {
    if (hidden.length === 1 && hidden[0].asset_id === pin.asset_id) setMenuOpen(false);
    // The store rolls back and records the error itself.
    unpin(pin.asset_id).catch(() => {});
  };

  const toggleMenu = (button: HTMLElement) => {
    const row = rowRef.current;
    if (row) {
      const rowBox = row.getBoundingClientRect();
      const box = button.getBoundingClientRect();
      // Open leftwards when there isn't room for the menu to the right.
      setMenuAlignRight(rowBox.right - box.left < 256);
    }
    setMenuOpen((o) => !o);
  };

  return (
    <div className="relative flex items-center gap-2 min-w-0 h-7" role="region" aria-label="Pinned assets">
      <span className="inline-flex items-center gap-1 shrink-0 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        <Pin className="w-3 h-3" />
        Pinned
      </span>

      <div ref={rowRef} className="flex-1 min-w-0 flex items-center gap-1.5">
        <div className="flex items-center gap-1.5 min-w-0 overflow-hidden">
          {pins.slice(0, fit).map((pin) => (
            <span
              key={pin.asset_id}
              className={`${PILL} hover:border-primary/40 hover:bg-primary/5 transition-colors ${pin.available === false ? 'opacity-70' : ''}`}
            >
              <button
                type="button"
                onClick={() => open(pin)}
                title={tooltipFor(pin)}
                className="inline-flex items-center gap-1 min-w-0 h-full pl-2 pr-1 rounded-l-full cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <PillBody pin={pin} />
              </button>
              <button
                type="button"
                onClick={() => remove(pin)}
                aria-label={`Unpin ${pin.title}`}
                title="Unpin"
                className="mr-1 p-0.5 rounded-full text-slate-300 hover:text-slate-600 hover:bg-slate-200 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>

        {hidden.length > 0 && (
          <div ref={menuRef} className="relative shrink-0">
            <button
              type="button"
              onClick={(e) => toggleMenu(e.currentTarget)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title={`${hidden.length} more pinned`}
              className={`${PILL} px-2 font-semibold text-slate-500 hover:border-primary/40 hover:text-primary cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40`}
            >
              +{hidden.length}
            </button>
            {menuOpen && (
              <div
                role="menu"
                className={`absolute top-full mt-1 z-30 w-64 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg p-1 ${
                  menuAlignRight ? 'right-0' : 'left-0'
                }`}
              >
                {hidden.map((pin) => {
                  const { Icon, tone } = iconFor(pin);
                  return (
                    <div key={pin.asset_id} className="flex items-center gap-1 rounded-lg hover:bg-slate-50">
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => open(pin)}
                        title={tooltipFor(pin)}
                        className="flex-1 min-w-0 flex items-start gap-2 px-2 py-1.5 text-left cursor-pointer"
                      >
                        <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${tone}`} />
                        <span className="min-w-0">
                          <span
                            className={`block text-xs font-medium truncate ${
                              pin.available === false ? 'line-through text-slate-400' : 'text-slate-800'
                            }`}
                          >
                            {pin.title}
                          </span>
                          {pin.subtitle && <span className="block text-[11px] text-slate-400 truncate">{pin.subtitle}</span>}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => remove(pin)}
                        aria-label={`Unpin ${pin.title}`}
                        title="Unpin"
                        className="mr-1 p-1 rounded-full text-slate-300 hover:text-slate-600 hover:bg-slate-200 cursor-pointer"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Off-screen mirror at natural widths, used to decide how many pills fit. */}
      <div aria-hidden className="absolute left-0 top-0 invisible pointer-events-none h-0 overflow-hidden">
        <div ref={mirrorRef} className="flex w-max gap-1.5">
          {pins.map((pin) => (
            <span key={pin.asset_id} className={PILL}>
              <span className="inline-flex items-center gap-1 min-w-0 h-full pl-2 pr-1">
                <PillBody pin={pin} />
              </span>
              <span className="mr-1 p-0.5">
                <X className="w-3 h-3" />
              </span>
            </span>
          ))}
        </div>
        <span ref={plusMirrorRef} className={`${PILL} px-2 font-semibold`}>
          +{pins.length}
        </span>
      </div>
    </div>
  );
}
