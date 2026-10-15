/**
 * `useViewScroll` — scroll handling for in-page views that change without a
 * route change (e.g. query-param drill-ins), applied to whichever element
 * actually scrolls the page (the layout's `main`, not `window`).
 *
 * On a navigation that changes `view`, back/forward returns the destination
 * history entry to where it was left (if it was showing the same view then);
 * anything else starts at the top. Navigations that keep `view` (e.g. opening
 * an overlay) and in-place changes of `view` leave the scroll alone. Arriving
 * on the page counts as a navigation.
 */
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

/** Nearest scrolling ancestor of `el`, or the document's scroller. */
export function scrollContainerOf(el: Element | null): HTMLElement {
    for (let node = el?.parentElement; node; node = node.parentElement) {
        const { overflowY } = getComputedStyle(node);
        if (overflowY === 'auto' || overflowY === 'scroll') return node;
    }
    return (document.scrollingElement ?? document.documentElement) as HTMLElement;
}

/** Where each history entry (by `location.key`) was left, and what it showed. */
const leftAt = new Map<string, { view: string; top: number }>();

const FOLLOW_MS = 1500;
const USER_SCROLL_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Scrolls to `top`. Content that is still loading may not be tall enough yet,
 * so keep retrying as `content` grows until it fits, the user takes over, or
 * `FOLLOW_MS` passes. Returns a function that stops retrying.
 */
function scrollToOffset(scroller: HTMLElement, content: Element, top: number): () => void {
    const reached = () => {
        scroller.scrollTop = top;
        return Math.abs(scroller.scrollTop - top) <= 1;
    };
    if (reached()) return () => {};

    const observer = new ResizeObserver(() => {
        if (reached()) stop();
    });
    const timer = window.setTimeout(() => stop(), FOLLOW_MS);
    function stop() {
        observer.disconnect();
        window.clearTimeout(timer);
        USER_SCROLL_EVENTS.forEach((type) => window.removeEventListener(type, stop, true));
    }
    observer.observe(content);
    USER_SCROLL_EVENTS.forEach((type) => window.addEventListener(type, stop, { capture: true, passive: true }));
    return stop;
}

export function useViewScroll(view: string, anchorRef: RefObject<HTMLElement | null>): void {
    const { key: entry } = useLocation();
    const navigationType = useNavigationType();
    const shown = useRef<{ entry: string; view: string } | null>(null);

    useLayoutEffect(() => {
        const anchor = anchorRef.current;
        if (!anchor) return undefined;
        const prev = shown.current;
        shown.current = { entry, view };
        if (prev && (prev.entry === entry || prev.view === view)) return undefined;

        const scroller = scrollContainerOf(anchor);
        const saved = navigationType === 'POP' ? leftAt.get(entry) : undefined;
        if (saved && saved.view === view) return scrollToOffset(scroller, anchor, saved.top);
        scroller.scrollTop = 0;
        return undefined;
    }, [anchorRef, entry, view, navigationType]);

    useEffect(() => {
        const anchor = anchorRef.current;
        if (!anchor) return undefined;
        const scroller = scrollContainerOf(anchor);
        const record = () => {
            const current = shown.current;
            if (current) leftAt.set(current.entry, { view: current.view, top: scroller.scrollTop });
        };
        scroller.addEventListener('scroll', record, { passive: true });
        return () => scroller.removeEventListener('scroll', record);
    }, [anchorRef]);
}
