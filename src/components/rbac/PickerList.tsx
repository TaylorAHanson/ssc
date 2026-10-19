import { useEffect, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Check } from 'lucide-react';
import { cn } from '../../lib/utils';
import { optionDomId } from './cellOptions';

export interface PickerItem {
  value: string;
  selected: boolean;
  /** An existing value that isn't one of the options; shown in red. */
  outside?: boolean;
  disabled?: boolean;
  note?: string;
  /** Items sharing a group sit under one heading. */
  group?: string;
}

const GAP = 4;
const MAX_HEIGHT = 288;
const MAX_WIDTH = 384;

/** Fixed coordinates under (or, near the bottom of the window, over) the anchor, kept in place on scroll and resize. */
function useAnchoredStyle(anchor: RefObject<HTMLElement | null>): CSSProperties {
  const [style, setStyle] = useState<CSSProperties>({ position: 'fixed', visibility: 'hidden' });
  useEffect(() => {
    const el = anchor.current;
    if (!el) return;
    let frame = 0;
    const place = () => {
      const r = el.getBoundingClientRect();
      const below = window.innerHeight - r.bottom - GAP * 2;
      const above = r.top - GAP * 2;
      const up = below < 160 && above > below;
      const left = Math.max(GAP, Math.min(r.left, window.innerWidth - r.width - GAP));
      setStyle({
        position: 'fixed',
        left,
        minWidth: r.width,
        maxWidth: Math.min(Math.max(r.width, MAX_WIDTH), window.innerWidth - left - GAP),
        maxHeight: Math.min(MAX_HEIGHT, up ? above : below),
        ...(up ? { bottom: window.innerHeight - r.top + GAP } : { top: r.bottom + GAP }),
      });
    };
    const schedule = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(place);
    };
    const observer = new ResizeObserver(place);
    observer.observe(el);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
    };
  }, [anchor]);
  return style;
}

/**
 * The option list of a cell editor. Focus stays on the editor (which points at the active
 * option with aria-activedescendant), so pressing here must not take focus away from it.
 */
export function PickerList({ anchor, id, label, items, active, variant, onPick, notice, footer }: {
  anchor: RefObject<HTMLElement | null>;
  id: string;
  label: string;
  items: PickerItem[];
  active: number;
  variant: 'select' | 'multi' | 'suggest';
  onPick: (index: number) => void;
  /** Shown after the options, e.g. why there are none to pick. */
  notice?: string;
  footer?: string;
}) {
  const style = useAnchoredStyle(anchor);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const ul = list.current;
    const li = ul?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (!ul || !li) return;
    if (li.offsetTop < ul.scrollTop) ul.scrollTop = li.offsetTop;
    else if (li.offsetTop + li.offsetHeight > ul.scrollTop + ul.clientHeight) ul.scrollTop = li.offsetTop + li.offsetHeight - ul.clientHeight;
  }, [active, style.maxHeight]);

  return createPortal(
    <div
      style={style}
      onMouseDown={(e) => e.preventDefault()}
      className="z-40 flex flex-col overflow-hidden rounded-md border border-gray-200 bg-white text-sm shadow-lg"
    >
      <ul
        ref={list}
        id={id}
        role="listbox"
        aria-label={label}
        aria-multiselectable={variant === 'multi' || undefined}
        className="relative min-h-0 flex-1 overflow-y-auto py-1"
      >
        {items.map((item, i) => (
          <PickerOption
            key={`${item.value}-${i}`}
            id={optionDomId(id, i)}
            index={i}
            item={item}
            heading={item.group && item.group !== items[i - 1]?.group ? item.group : null}
            active={i === active}
            variant={variant}
            onPick={onPick}
          />
        ))}
        {notice && <li role="presentation" className="px-2.5 py-2 text-xs text-gray-500">{notice}</li>}
      </ul>
      {footer && <p className="border-t border-gray-100 px-2.5 py-1 text-[11px] text-gray-500">{footer}</p>}
    </div>,
    document.body,
  );
}

function PickerOption({ id, index, item, heading, active, variant, onPick }: {
  id: string;
  index: number;
  item: PickerItem;
  heading: string | null;
  active: boolean;
  variant: 'select' | 'multi' | 'suggest';
  onPick: (index: number) => void;
}) {
  return (
    <>
      {heading && (
        <li role="presentation" className="px-2.5 pb-0.5 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400">
          {heading}
        </li>
      )}
      <li
        id={id}
        data-index={index}
        role="option"
        aria-selected={variant === 'suggest' ? active : item.selected}
        aria-disabled={item.disabled || undefined}
        onClick={() => !item.disabled && onPick(index)}
        className={cn(
          'flex items-start gap-2 px-2.5 py-1',
          item.disabled ? 'cursor-not-allowed text-gray-400' : 'cursor-pointer hover:bg-gray-50',
          active && 'bg-gray-100 hover:bg-gray-100',
        )}
      >
        {variant === 'multi' && (
          <span
            aria-hidden="true"
            className={cn(
              'mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border',
              item.selected ? 'border-primary bg-primary text-white' : 'border-gray-300 bg-white',
            )}
          >
            {item.selected && <Check className="h-3 w-3" />}
          </span>
        )}
        {variant === 'select' && (
          <Check aria-hidden="true" className={cn('mt-0.5 h-3.5 w-3.5 shrink-0 text-primary', !item.selected && 'invisible')} />
        )}
        <span className="min-w-0">
          <span className={cn('break-words', item.selected && variant !== 'suggest' && 'font-medium', item.outside && !item.disabled && 'text-red-800')}>
            {item.outside && <AlertTriangle aria-hidden="true" className="-mt-0.5 mr-1 inline h-3 w-3" />}
            {item.value}
          </span>
          {item.note && <span className="block text-[11px] text-gray-500">{item.note}</span>}
        </span>
      </li>
    </>
  );
}
