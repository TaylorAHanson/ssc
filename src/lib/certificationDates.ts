import { parseISO } from 'date-fns';

// The backend serializes naive UTC datetimes (no timezone suffix). Treat any
// such string as UTC so date-fns renders it in the viewer's local timezone.
export const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

// Render a UTC timestamp in US Pacific time with an explicit tz label. Uses the
// America/Los_Angeles zone so the abbreviation auto-switches between PST and PDT
// with daylight saving rather than being hardcoded.
export const formatPacific = (value: string): string =>
  parseUtc(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/Los_Angeles',
    timeZoneName: 'short',
  });
