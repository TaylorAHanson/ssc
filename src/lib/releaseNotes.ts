/**
 * Release notes, parsed from the repo-root `RELEASE_NOTES.md` (the single
 * source of truth — edit that file, not this one).
 *
 * Each `## <major.minor.patch> — <YYYY-MM-DD>` heading starts a release; the
 * markdown until the next such heading is its body. Text above the first
 * release heading is authoring guidance and is not shown in the app.
 */
import rawNotes from '../../RELEASE_NOTES.md?raw';

export interface ReleaseNote {
  version: string;
  /** ISO date (`YYYY-MM-DD`), or null when the heading has none. */
  date: string | null;
  /** Markdown body, without the version heading. */
  body: string;
}

const RELEASE_HEADING_RE = /^##\s+v?(\d+\.\d+\.\d+)\s*(?:[—–-]+\s*(\d{4}-\d{2}-\d{2}))?\s*$/;

export function parseReleaseNotes(markdown: string): ReleaseNote[] {
  const releases: ReleaseNote[] = [];
  let current: { version: string; date: string | null; lines: string[] } | null = null;

  const flush = () => {
    if (current) {
      releases.push({
        version: current.version,
        date: current.date,
        body: current.lines.join('\n').trim(),
      });
    }
  };

  for (const line of markdown.split(/\r?\n/)) {
    const match = RELEASE_HEADING_RE.exec(line);
    if (match) {
      flush();
      current = { version: match[1], date: match[2] ?? null, lines: [] };
    } else if (current) {
      current.lines.push(line);
    }
  }
  flush();
  return releases;
}

export const RELEASE_NOTES: ReleaseNote[] = parseReleaseNotes(rawNotes);

/** The newest release in `RELEASE_NOTES.md`, kept in sync with `package.json`. */
export const CURRENT_VERSION: string | null = RELEASE_NOTES[0]?.version ?? null;

/** DOM id / URL hash for a release section, e.g. `v1.0.0`. */
export function releaseAnchorId(version: string): string {
  return `v${version}`;
}
