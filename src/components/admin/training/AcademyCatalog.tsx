import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Link2, Loader2, RefreshCw, Search, X } from 'lucide-react';
import { Button } from '../../ui/button';
import { Card, CardContent } from '../../ui/card';
import {
  adminAcademyCatalogStatus, adminAcademySuggestions, adminLinkAcademyCourse, adminSearchAcademyCatalog,
  adminSyncTrainingCatalog,
  type AcademyCatalogStatus, type AcademyCourse, type AcademySuggestion, type AcademySuggestions,
} from '../../../services/api';
import { errorMessage, plural, relativeTime } from '../metadata/format';
import { useDebounced } from '../metadata/useAsync';
import { settingsGroupPath, TRAINING_GROUP } from '../../../pages/admin/settingsGroups';
import { academyMeta } from './academy';

const REASONS: Record<AcademySuggestion['reason'], string> = {
  same_code: 'Same course code',
  newer_version: 'Newer version of the same course',
  similar_title: 'Similar title',
};

/** Academy sync status, the Sync button, and courses that need linking. */
export function AcademyCatalogPanel({ onChanged, onError }: { onChanged: () => void; onError: (msg: string) => void }) {
  const [status, setStatus] = useState<AcademyCatalogStatus | null>(null);
  const [suggestions, setSuggestions] = useState<AcademySuggestions | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [linking, setLinking] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState(false);

  const refresh = async () => {
    try {
      const [s, sug] = await Promise.all([adminAcademyCatalogStatus(), adminAcademySuggestions()]);
      setStatus(s);
      setSuggestions(sug);
    } catch (e) {
      onError(errorMessage(e, 'Failed to load the Academy catalog status'));
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sync = async () => {
    setSyncing(true);
    try {
      const res = await adminSyncTrainingCatalog();
      if (!res.ok) onError(`Academy sync failed: ${res.error || 'unknown error'}`);
      await refresh();
      onChanged();
    } catch (e) {
      onError(errorMessage(e, 'Academy sync failed'));
    } finally {
      setSyncing(false);
    }
  };

  const link = async (items: AcademySuggestion[]) => {
    setLinking(new Set(items.map((s) => s.course_id)));
    try {
      for (const s of items) await adminLinkAcademyCourse(s.course_id, s.match.id);
      await refresh();
      onChanged();
    } catch (e) {
      onError(errorMessage(e, 'Failed to link the course'));
    } finally {
      setLinking(new Set());
    }
  };

  if (!status) return null;
  const last = status.last_sync;
  const lastOk = status.last_successful_sync;
  const pending = suggestions?.suggestions || [];
  const unmatched = suggestions?.unmatched || [];
  const attention = pending.length + unmatched.length;

  return (
    <Card>
      <CardContent className="p-0">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <div className="flex-1 min-w-0 text-sm">
            <span className="font-semibold text-gray-800">Databricks Academy catalog</span>
            <span className="text-gray-500">
              {' · '}
              {lastOk
                ? `${plural(status.catalog_courses, 'course')}, synced ${relativeTime(lastOk.finished_at)}`
                : 'not synced yet'}
              {' · '}
              {status.sync_cron ? 'syncs on a schedule' : 'manual sync only'}
              {' · '}
              {status.courses_linked} of {status.courses_total} courses linked
            </span>
            {last && !last.ok && (
              <div className="mt-1 flex items-center gap-1.5 text-xs text-red-600">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                Last sync {relativeTime(last.started_at)} failed: {last.error}
              </div>
            )}
          </div>
          <Link to={settingsGroupPath(TRAINING_GROUP)} className="text-xs text-primary hover:underline">Settings</Link>
          <Button variant="outline" size="sm" onClick={sync} disabled={syncing || status.syncing}>
            <RefreshCw className={`w-4 h-4 mr-2 ${syncing || status.syncing ? 'animate-spin' : ''}`} />
            {syncing || status.syncing ? 'Syncing…' : 'Sync Academy catalog'}
          </Button>
        </div>

        {lastOk && attention > 0 && (
          <div className="border-t border-gray-100">
            <div className="flex items-center gap-2 px-4 py-2 text-sm text-amber-800 bg-amber-50/60">
              <button onClick={() => setOpen((v) => !v)} className="flex-1 flex items-center gap-2 text-left">
                {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                {plural(attention, 'course')} not linked to a current Academy course
                {pending.length > 0 && ` · ${pending.length} with a suggested match`}
              </button>
              {open && pending.length > 1 && (
                <button
                  onClick={() => link(pending)}
                  disabled={linking.size > 0}
                  className="text-xs font-semibold text-primary hover:underline disabled:opacity-50"
                >
                  Link all suggested
                </button>
              )}
            </div>
            {open && (
              <div className="max-h-96 overflow-y-auto divide-y divide-gray-100">
                {pending.map((s) => (
                  <div key={s.course_id} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <div className="flex-1 min-w-0">
                      <div className="truncate text-gray-800">
                        {s.course_title}
                        {s.retired && <span className="ml-2 text-[10px] font-bold uppercase text-amber-700">Retired</span>}
                        <span className="text-gray-400"> · {s.track_name}</span>
                      </div>
                      <div className="truncate text-xs text-gray-500">
                        {REASONS[s.reason]}: <span className="text-gray-700">{s.match.name}</span>
                        {' '}<span className="font-mono text-[11px]">{s.match.code}</span> · {academyMeta(s.match)}
                      </div>
                    </div>
                    <Button size="sm" variant="outline" disabled={linking.size > 0} onClick={() => link([s])}>
                      {linking.has(s.course_id) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5 mr-1.5" />}
                      Link
                    </Button>
                  </div>
                ))}
                {unmatched.length > 0 && (
                  <div className="px-4 py-2 text-xs text-gray-500">
                    <div className="mb-1">
                      No match found for these. Edit one and use <span className="font-medium">Pick from Academy</span>, or
                      leave it as a custom course.
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {unmatched.map((u) => (
                        <span key={u.course_id} className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-gray-600">
                          {u.course_title}
                          {u.retired && <span className="ml-1 text-amber-700">(retired)</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Search the synced Academy catalog and pick a course. */
export function AcademyCoursePicker({ onPick }: { onPick: (c: AcademyCourse) => void }) {
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<{ query: string; rows: AcademyCourse[] } | null>(null);
  const [open, setOpen] = useState(false);
  const debounced = useDebounced(query, 250);
  const box = useRef<HTMLDivElement>(null);
  const results = found?.rows || [];
  const loading = found?.query !== debounced;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    adminSearchAcademyCatalog(debounced, 30)
      .then((rows) => { if (!cancelled) setFound({ query: debounced, rows }); })
      .catch(() => { if (!cancelled) setFound({ query: debounced, rows: [] }); });
    return () => { cancelled = true; };
  }, [debounced, open]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  return (
    <div ref={box} className="relative">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          className="w-full border border-gray-300 rounded-md h-9 pl-8 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          placeholder="Pick from Academy: search by course name or code"
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        />
        {query && (
          <button className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400" onClick={() => setQuery('')}>
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute z-20 mt-1 w-full max-h-80 overflow-y-auto rounded-md border border-gray-200 bg-white shadow-lg">
          {loading && results.length === 0 ? (
            <div className="px-3 py-4 text-center text-sm text-gray-400"><Loader2 className="w-4 h-4 animate-spin inline" /></div>
          ) : results.length === 0 ? (
            <div className="px-3 py-4 text-center text-sm text-gray-400">
              No Academy courses match. If the catalog hasn't synced yet, use Sync Academy catalog above.
            </div>
          ) : (
            results.map((c) => (
              <button
                key={c.id}
                className="w-full text-left px-3 py-2 hover:bg-gray-50 border-b border-gray-50 last:border-0"
                onClick={() => { onPick(c); setOpen(false); setQuery(''); }}
              >
                <div className="text-sm text-gray-900">{c.name}</div>
                <div className="text-[11px] text-gray-500">
                  <span className="font-mono">{c.code}</span> · {academyMeta(c)}
                </div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** Small badge for a course row: linked, retired in Academy, or nothing. */
export function AcademyBadge({ academy }: { academy?: AcademyCourse | null }) {
  if (!academy) return null;
  if (!academy.active) {
    return (
      <span
        title="Academy no longer lists this course and no newer version was found. Link it to a current course."
        className="text-[9px] uppercase font-bold text-amber-700 border border-amber-200 bg-amber-50 rounded px-1"
      >
        Retired
      </span>
    );
  }
  return (
    <a
      href={academy.url || undefined}
      target="_blank"
      rel="noreferrer"
      title={`Linked to ${academy.code}. Code, link, type and duration update on every sync.`}
      className="inline-flex items-center gap-0.5 text-[9px] uppercase font-bold text-emerald-700 border border-emerald-200 bg-emerald-50 rounded px-1"
    >
      Academy <ExternalLink className="w-2.5 h-2.5" />
    </a>
  );
}
