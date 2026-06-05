/**
 * Lightweight client-side diagnostics capture for bug reports.
 *
 * Maintains small ring buffers of recent console output and failed network
 * requests so a bug report can attach "what was happening" without any external
 * logging service. Nothing is sent anywhere automatically — `getDiagnostics()`
 * is only read when the user chooses to attach diagnostics to a bug report.
 *
 * Privacy: we capture console message text and request URLs + status codes, but
 * never request/response bodies. Call `initDiagnostics()` once at app startup.
 */

export interface ConsoleEntry {
  level: string;
  message: string;
  ts: string;
}

export interface NetworkEntry {
  method: string;
  url: string;
  status: number;
  status_text: string;
  ts: string;
}

export interface Diagnostics {
  console_logs: ConsoleEntry[];
  network_errors: NetworkEntry[];
  user_agent: string;
  page_url: string;
  app_version: string;
}

const MAX_CONSOLE = 60;
const MAX_NETWORK = 30;
const MAX_MSG_LEN = 1000;

const consoleBuffer: ConsoleEntry[] = [];
const networkBuffer: NetworkEntry[] = [];

let initialized = false;

export const APP_VERSION: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_APP_VERSION || 'dev';

function pushCapped<T>(buf: T[], item: T, max: number): void {
  buf.push(item);
  if (buf.length > max) buf.splice(0, buf.length - max);
}

function stringifyArg(arg: unknown): string {
  if (typeof arg === 'string') return arg;
  if (arg instanceof Error) return `${arg.name}: ${arg.message}\n${arg.stack || ''}`;
  try {
    return JSON.stringify(arg);
  } catch {
    return String(arg);
  }
}

function record(level: string, args: unknown[]): void {
  try {
    const message = args.map(stringifyArg).join(' ').slice(0, MAX_MSG_LEN);
    pushCapped(consoleBuffer, { level, message, ts: new Date().toISOString() }, MAX_CONSOLE);
  } catch {
    // Never let diagnostics capture break the app.
  }
}

/**
 * Patch console methods + global error handlers + fetch to populate the ring
 * buffers. Safe to call multiple times (no-ops after the first).
 */
export function initDiagnostics(): void {
  if (initialized || typeof window === 'undefined') return;
  initialized = true;

  // --- Console ---
  const levels: Array<'log' | 'info' | 'warn' | 'error' | 'debug'> = [
    'log',
    'info',
    'warn',
    'error',
    'debug',
  ];
  for (const level of levels) {
    const original = console[level]?.bind(console);
    if (!original) continue;
    console[level] = (...args: unknown[]) => {
      record(level, args);
      original(...args);
    };
  }

  // --- Uncaught errors ---
  window.addEventListener('error', (event) => {
    const msg = event.error
      ? stringifyArg(event.error)
      : `${event.message} (${event.filename}:${event.lineno}:${event.colno})`;
    record('uncaught', [msg]);
  });

  // --- Unhandled promise rejections ---
  window.addEventListener('unhandledrejection', (event) => {
    record('unhandledrejection', [stringifyArg(event.reason)]);
  });

  // --- Network failures (status >= 400 and thrown errors) ---
  if (typeof window.fetch === 'function') {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args: Parameters<typeof fetch>) => {
      const [input, init] = args;
      const method = (init?.method || (typeof input !== 'string' && 'method' in input ? input.method : 'GET') || 'GET').toUpperCase();
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      try {
        const response = await originalFetch(...args);
        if (!response.ok) {
          pushCapped(
            networkBuffer,
            {
              method,
              url,
              status: response.status,
              status_text: response.statusText,
              ts: new Date().toISOString(),
            },
            MAX_NETWORK,
          );
        }
        return response;
      } catch (err) {
        pushCapped(
          networkBuffer,
          {
            method,
            url,
            status: 0,
            status_text: err instanceof Error ? err.message : 'Network error',
            ts: new Date().toISOString(),
          },
          MAX_NETWORK,
        );
        throw err;
      }
    };
  }
}

/** Snapshot the current diagnostics for attaching to a bug report. */
export function getDiagnostics(): Diagnostics {
  return {
    console_logs: [...consoleBuffer],
    network_errors: [...networkBuffer],
    user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
    page_url: typeof window !== 'undefined' ? window.location.href : '',
    app_version: APP_VERSION,
  };
}
