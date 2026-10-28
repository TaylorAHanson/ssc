/**
 * Page views and browser errors, reported to the app's own server log.
 *
 * The server sees API calls but not which screen a person is on, nor what broke in
 * their browser. Events are queued and sent in small batches, and dropped while the
 * admin setting (Infrastructure -> Logging) is off. Only the page path, error text and
 * stack are sent: no page content, chat text or query results.
 */
import { postClientEvents, type ClientEventPayload } from '../services/api';
import { useBrandingStore } from '../stores/brandingStore';
import { APP_VERSION } from './diagnostics';

const FLUSH_DELAY_MS = 2000;
const MAX_BATCH = 10;
const MAX_QUEUE = 50;
// Per page load: a render loop can throw the same error hundreds of times.
const MAX_ERRORS = 20;
const MAX_MESSAGE = 1000;
const MAX_STACK = 3000;

type ErrorSource = 'react' | 'window.onerror' | 'unhandledrejection';

const queue: ClientEventPayload[] = [];
const seenErrors = new Set<string>();
let errorCount = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let initialized = false;

function scheduleFlush(): void {
  if (timer === null) timer = setTimeout(flush, FLUSH_DELAY_MS);
}

function flush(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  const { hasLoaded, clientEventsEnabled } = useBrandingStore.getState();
  if (!hasLoaded) {
    if (queue.length) scheduleFlush();
    return;
  }
  if (!clientEventsEnabled) {
    queue.length = 0;
    return;
  }
  while (queue.length) {
    void postClientEvents(queue.splice(0, MAX_BATCH));
  }
}

function enqueue(event: Omit<ClientEventPayload, 'app_version'>): void {
  if (queue.length >= MAX_QUEUE) return;
  queue.push({ ...event, app_version: APP_VERSION });
  scheduleFlush();
}

function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (typeof error === 'string') return error;
  try {
    return JSON.stringify(error) ?? String(error);
  } catch {
    return String(error);
  }
}

export function recordPageView(path: string): void {
  enqueue({ type: 'page_view', path });
}

export function reportClientError(source: ErrorSource, error: unknown, stack?: string): void {
  const message = describe(error).slice(0, MAX_MESSAGE);
  const key = `${source}|${message}`;
  if (seenErrors.has(key) || errorCount >= MAX_ERRORS) return;
  seenErrors.add(key);
  errorCount += 1;
  enqueue({
    type: 'error',
    path: window.location.pathname,
    source,
    message,
    stack: (stack || (error instanceof Error ? error.stack : undefined))?.slice(0, MAX_STACK),
  });
}

/** Report uncaught errors and flush on tab close. Safe to call more than once. */
export function initClientEvents(): void {
  if (initialized || typeof window === 'undefined') return;
  initialized = true;
  window.addEventListener('error', (event) => {
    reportClientError(
      'window.onerror',
      event.error ?? `${event.message} (${event.filename}:${event.lineno}:${event.colno})`,
    );
  });
  window.addEventListener('unhandledrejection', (event) => {
    reportClientError('unhandledrejection', event.reason);
  });
  window.addEventListener('pagehide', flush);
}
