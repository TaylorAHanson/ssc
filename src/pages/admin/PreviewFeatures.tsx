import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { parseISO, formatDistanceToNow } from 'date-fns';
import {
  AlertTriangle,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FlaskConical,
  Loader2,
  Newspaper,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { api } from '../../services/api';
import type {
  PreviewFeature,
  PreviewFeaturesResponse,
  PreviewTarget,
  TargetWorkspace,
} from '../../services/api';
import { useVisibleInterval } from '../../hooks/useVisibleInterval';

const ACCOUNT_TARGET = '__account__';
const LAST_VISIT_KEY = 'preview-features-last-visit';
const DOCS_SEARCH = 'https://docs.databricks.com/aws/en/search?q=';
// Every row action button is the same width so the Actions column lines up.
const ACTION_WIDTH = 'w-36';

const PHASE_LABELS: Record<string, string> = {
  BETA: 'Beta',
  PUBLIC_PREVIEW: 'Public Preview',
  PRIVATE_PREVIEW: 'Private Preview',
  GA: 'GA',
};

const PHASE_STYLES: Record<string, string> = {
  BETA: 'bg-purple-50 text-purple-700 border-purple-200',
  PUBLIC_PREVIEW: 'bg-sky-50 text-sky-700 border-sky-200',
  PRIVATE_PREVIEW: 'bg-amber-50 text-amber-700 border-amber-200',
  GA: 'bg-gray-50 text-gray-600 border-gray-200',
};

// The backend serializes naive UTC datetimes (no timezone suffix).
const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

const ago = (value?: string | null): string =>
  value ? formatDistanceToNow(parseUtc(value), { addSuffix: true }) : 'never';

type ChipTone = 'gray' | 'amber' | 'blue' | 'green' | 'red' | 'teal';

const TONE_STYLES: Record<ChipTone, string> = {
  gray: 'bg-gray-50 text-gray-500 border-gray-200',
  amber: 'bg-amber-50 text-amber-800 border-amber-200',
  blue: 'bg-blue-50 text-blue-800 border-blue-200',
  green: 'bg-green-50 text-green-800 border-green-200',
  red: 'bg-red-50 text-red-800 border-red-200',
  teal: 'bg-teal-50 text-teal-800 border-teal-200',
};

interface Chip {
  key: string;
  label: string;
  tone: ChipTone;
  title?: string;
}

/** The status chip for one target, combining the request status with what the sync observed. */
function chipFor(t: PreviewTarget | undefined): Chip {
  if (!t) return { key: 'not_available', label: 'Not available', tone: 'gray', title: 'Not listed in this workspace' };
  const disabling = t.action === 'disable';
  const eff = t.observed_value?.effective;
  if (t.target !== ACCOUNT_TARGET && !t.available && t.status !== 'implemented') {
    return { key: 'not_available', label: 'Not available', tone: 'gray', title: 'Not listed in this workspace' };
  }
  switch (t.status) {
    case 'requested':
      return { key: 'requested', label: disabling ? 'Turn-off requested' : 'Requested', tone: 'amber' };
    case 'approved':
      return {
        key: 'approved',
        label: disabling ? 'Turn-off approved' : 'Approved',
        tone: 'blue',
        title: t.note || undefined,
      };
    case 'implemented':
      if (t.drift) {
        return { key: 'drift', label: 'Drift ⚠', tone: 'red', title: 'Implemented, but the setting is now off' };
      }
      if (t.verification === 'attested') {
        return { key: 'implemented', label: 'Implemented (attested)', tone: 'green', title: `Confirmed by ${t.implemented_by || 'the implementer'}` };
      }
      return { key: 'implemented', label: 'Implemented ✓', tone: 'green', title: `Verified (${t.verification || 'api'})` };
    case 'rejected':
      return { key: 'rejected', label: 'Rejected', tone: 'red', title: t.note || undefined };
    default:
      if (eff === true) {
        return {
          key: 'already_on',
          label: 'Already on',
          tone: 'teal',
          title: t.observed_value?.set_here ? 'Turned on in this workspace' : 'On by default or from the account',
        };
      }
      if (t.observe_error && eff == null) {
        return { key: 'unreadable', label: 'Unreadable', tone: 'gray', title: t.observe_error };
      }
      return { key: 'not_requested', label: 'Not requested', tone: 'gray', title: t.note || undefined };
  }
}

function StatusChip({ chip }: { chip: Chip }) {
  return (
    <span
      title={chip.title}
      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-xs ${TONE_STYLES[chip.tone]}`}
    >
      {chip.label}
    </span>
  );
}

function PhaseChip({ phase }: { phase?: string | null }) {
  if (!phase) return null;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${PHASE_STYLES[phase] || PHASE_STYLES.GA}`}>
      {PHASE_LABELS[phase] || phase}
    </span>
  );
}

/** Mirrors the backend's eligibility check so the dialog can explain disabled boxes. */
function ineligibleReason(t: PreviewTarget | undefined, action: 'enable' | 'disable'): string | null {
  if (!t || (t.target !== ACCOUNT_TARGET && !t.available)) return 'Not listed in this workspace';
  if (t.status === 'requested' || t.status === 'approved') return 'A request is already in progress';
  const eff = t.observed_value?.effective;
  if (action === 'enable') {
    if (t.status === 'implemented' && !t.drift) return 'Already implemented';
    if (eff === true && t.status !== 'implemented') return 'Already on';
    return null;
  }
  if (!(t.status === 'implemented' || eff === true)) return 'Not on';
  return null;
}

function targetMap(f: PreviewFeature): Record<string, PreviewTarget> {
  return Object.fromEntries(f.targets.map((t) => [t.target, t]));
}

// Links come from Databricks metadata and the docs feed. Only https URLs are
// rendered as links (the backend drops anything else too).
const safeHref = (url?: string | null): string | undefined =>
  url && /^https:\/\/[^\s]+$/i.test(url) ? url : undefined;

/** Close a dialog on Escape. */
function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
}

const firstLine = (text?: string | null): string => {
  if (!text) return '';
  const sentence = text.split(/(?<=[.!?])\s/)[0];
  return sentence.length > 160 ? `${sentence.slice(0, 157)}…` : sentence;
};

// ---------------------------------------------------------------------------
// Request dialog
// ---------------------------------------------------------------------------

interface RequestDialogProps {
  feature: PreviewFeature;
  action: 'enable' | 'disable';
  workspaces: TargetWorkspace[];
  onClose: () => void;
  onDone: (message: { text: string; requestId?: string }) => void;
}

function RequestDialog({ feature, action, workspaces, onClose, onDone }: RequestDialogProps) {
  useEscape(onClose);
  const targets = targetMap(feature);
  const isAccount = feature.scope === 'account';
  const options = workspaces.map((w) => ({ ws: w, reason: ineligibleReason(targets[w.name], action) }));
  const [selected, setSelected] = useState<string[]>(
    options.filter((o) => !o.reason).length === 1 ? options.filter((o) => !o.reason).map((o) => o.ws.name) : [],
  );
  const [justification, setJustification] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountReason = isAccount ? ineligibleReason(targets[ACCOUNT_TARGET] ?? {
    target: ACCOUNT_TARGET, available: true, status: 'not_requested', drift: false,
  }, action) : null;

  const toggle = (name: string) =>
    setSelected((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name]));

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.requestPreviewFeature(feature.id, {
        action,
        targets: isAccount ? [] : selected,
        justification: justification.trim() || undefined,
      });
      onDone({
        text: `${action === 'enable' ? 'Request' : 'Turn-off request'} for ${feature.display_name} sent for approval.`,
        requestId: res.request_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = !submitting && (isAccount ? !accountReason : selected.length > 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">
              {action === 'enable' ? 'Request' : 'Request turn off'}: {feature.display_name}
            </h3>
            <p className="mt-1 text-sm text-gray-500">
              {isAccount
                ? 'Account-level preview. After approval, an account admin makes the change in the account console.'
                : action === 'enable'
                  ? 'One approval covers every workspace you select. After approval, each workspace’s service principal turns it on.'
                  : 'One approval covers every workspace you select. It stays explicitly off afterwards; it can’t go back to “inherited”.'}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 px-5 py-4">
          {!isAccount && (
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">Workspaces</span>
                <button
                  type="button"
                  className="text-xs text-primary hover:underline"
                  onClick={() => setSelected(options.filter((o) => !o.reason).map((o) => o.ws.name))}
                >
                  Select all eligible
                </button>
              </div>
              <div className="max-h-56 space-y-1 overflow-y-auto rounded border p-2">
                {options.length === 0 && <p className="text-sm text-gray-500">No target workspaces are configured.</p>}
                {options.map(({ ws, reason }) => (
                  <label
                    key={ws.name}
                    title={reason || undefined}
                    className={`flex items-center gap-2 rounded px-2 py-1 text-sm ${reason ? 'cursor-not-allowed text-gray-400' : 'cursor-pointer hover:bg-gray-50'}`}
                  >
                    <input
                      type="checkbox"
                      disabled={!!reason}
                      checked={selected.includes(ws.name)}
                      onChange={() => toggle(ws.name)}
                    />
                    <span className="font-medium">{ws.name}</span>
                    <span className="text-xs text-gray-400">{ws.environment}</span>
                    {reason && <span className="ml-auto text-xs">{reason}</span>}
                  </label>
                ))}
              </div>
            </div>
          )}
          {isAccount && accountReason && (
            <p className="rounded bg-gray-50 p-2 text-sm text-gray-600">Can’t request this: {accountReason.toLowerCase()}.</p>
          )}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Justification</label>
            <Textarea
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="Why is this needed? Approvers see this."
              rows={3}
            />
          </div>
          {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t px-5 py-3">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {action === 'enable' ? 'Submit request' : 'Submit turn-off request'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Approve dialog
// ---------------------------------------------------------------------------

interface ApprovableRequest {
  requestId: string;
  action: 'enable' | 'disable';
  targets: string[];
  requestedBy?: string | null;
}

/** Open requests on this feature the current user may approve (not manual tasks). */
function approvableRequests(f: PreviewFeature): ApprovableRequest[] {
  const byId: Record<string, ApprovableRequest> = {};
  for (const t of f.targets) {
    if (!t.request_id || !t.can_approve || t.status !== 'requested' || t.pending_approval === 'manual_task') continue;
    const r = (byId[t.request_id] ??= {
      requestId: t.request_id,
      action: t.action === 'disable' ? 'disable' : 'enable',
      targets: [],
      requestedBy: t.requested_by,
    });
    r.targets.push(t.target === ACCOUNT_TARGET ? 'Account' : t.target);
  }
  return Object.values(byId);
}

function ApproveDialog({
  feature,
  onClose,
  onDone,
}: {
  feature: PreviewFeature;
  onClose: () => void;
  onDone: (message: { text: string; requestId?: string }) => void;
}) {
  useEscape(onClose);
  const requests = approvableRequests(feature);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const approve = async (r: ApprovableRequest) => {
    setBusy(r.requestId);
    setError(null);
    try {
      await api.approveRequest(r.requestId);
      onDone({ text: `Approved ${feature.display_name} for ${r.targets.join(', ')}.`, requestId: r.requestId });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Approve: {feature.display_name}</h3>
            <p className="mt-1 text-sm text-gray-500">
              Approving lets the workflow continue: workspace previews are changed automatically, and anything
              it can’t change becomes an Implement task. The full report and the requester’s justification are in{' '}
              <Link to="/approvals" className="text-primary hover:underline">Pending Approvals</Link>.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-3 px-5 py-4">
          {requests.length === 0 && <p className="text-sm text-gray-500">Nothing here is waiting for your approval.</p>}
          {requests.map((r) => (
            <div key={r.requestId} className="flex items-center justify-between gap-3 rounded border p-3 text-sm">
              <div>
                <div className="font-medium text-gray-900">
                  {r.action === 'enable' ? 'Turn on' : 'Turn off'} for {r.targets.join(', ')}
                </div>
                <div className="text-xs text-gray-500">
                  Requested by {r.requestedBy || 'unknown'} ·{' '}
                  <Link to={`/requests/${r.requestId}`} className="text-primary hover:underline">View request</Link>
                </div>
              </div>
              <Button size="sm" className={ACTION_WIDTH} disabled={!!busy} onClick={() => approve(r)}>
                {busy === r.requestId && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Approve
              </Button>
            </div>
          ))}
          {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end border-t px-5 py-3">
          <Button variant="outline" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expanded row
// ---------------------------------------------------------------------------

function DocsLinks({ feature }: { feature: PreviewFeature }) {
  return (
    <div className="flex flex-wrap items-center gap-4 text-sm">
      {safeHref(feature.docs_link) ? (
        <a href={safeHref(feature.docs_link)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
          <BookOpen className="h-4 w-4" /> Docs
          {feature.docs_link_source === 'sitemap' && <span className="text-xs text-gray-400">(suggested)</span>}
          <ExternalLink className="h-3 w-3" />
        </a>
      ) : (
        <span className="inline-flex items-center gap-1 text-gray-500">
          <BookOpen className="h-4 w-4" /> No documentation found ·{' '}
          <a
            href={`${DOCS_SEARCH}${encodeURIComponent(feature.display_name)}`}
            target="_blank"
            rel="noreferrer"
            className="text-primary hover:underline"
          >
            search the docs
          </a>
        </span>
      )}
      {safeHref(feature.announcement_url) && (
        <a href={safeHref(feature.announcement_url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
          <Newspaper className="h-4 w-4" /> Release note
          {feature.announced_at && <span className="text-xs text-gray-400">({parseUtc(feature.announced_at).toLocaleDateString()})</span>}
          <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </div>
  );
}

function FeatureDetails({
  feature,
  onSaved,
}: {
  feature: PreviewFeature;
  onSaved: (f: PreviewFeature) => void;
}) {
  const [docsLink, setDocsLink] = useState(feature.docs_link_source === 'admin' ? feature.docs_link || '' : '');
  const [probePath, setProbePath] = useState(feature.probe?.path || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (body: Parameters<typeof api.updatePreviewFeature>[1]) => {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updatePreviewFeature(feature.id, body));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const withRequests = feature.targets.filter((t) => t.request_id || t.note || t.observe_error);

  return (
    <div className="space-y-4 bg-gray-50/60 px-4 py-4 text-sm">
      <div className="space-y-2">
        {feature.description && <p className="whitespace-pre-line text-gray-800">{feature.description}</p>}
        {feature.announcement_text && feature.announcement_text !== feature.description && (
          <div className="rounded border border-gray-200 bg-white p-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Announcement</div>
            <p className="whitespace-pre-line text-gray-700">{feature.announcement_text}</p>
          </div>
        )}
        <DocsLinks feature={feature} />
        <div className="flex flex-wrap gap-4 text-xs text-gray-500">
          {feature.setting_name && <span>Setting: <code>{feature.setting_name}</code></span>}
          <span>First seen {ago(feature.first_seen_at)}</span>
          {feature.phase_changed_at && <span>Phase changed {ago(feature.phase_changed_at)}</span>}
          <span>Scope: {feature.scope} ({feature.scope_source === 'admin' ? 'set by an admin' : feature.scope_source === 'inferred' ? 'from the release notes' : 'listed by the workspace'})</span>
          {feature.value_type && feature.value_type !== 'boolean' && feature.setting_name && (
            <span className="text-amber-700">Not an on/off setting: changes are made by hand</span>
          )}
        </div>
      </div>

      {withRequests.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Requests and notes</div>
          <table className="w-full text-xs">
            <thead className="text-left text-gray-500">
              <tr>
                <th className="py-1 pr-3 font-medium">Target</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 pr-3 font-medium">Requested / approved / implemented by</th>
                <th className="py-1 pr-3 font-medium">Note</th>
                <th className="py-1 font-medium">Request</th>
              </tr>
            </thead>
            <tbody>
              {withRequests.map((t) => (
                <tr key={t.target} className="border-t border-gray-200 align-top">
                  <td className="py-1 pr-3 font-medium">{t.target === ACCOUNT_TARGET ? 'Account' : t.target}</td>
                  <td className="py-1 pr-3"><StatusChip chip={chipFor(t)} /></td>
                  <td className="py-1 pr-3 text-gray-600">
                    {[t.requested_by, t.approved_by, t.implemented_by].map((v) => v || '—').join(' / ')}
                  </td>
                  <td className="py-1 pr-3 text-gray-600">{t.note || t.observe_error || ''}</td>
                  <td className="py-1">
                    {t.request_id && (
                      <Link to={`/requests/${t.request_id}`} className="text-primary hover:underline">
                        {t.request_status || 'open'}
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {feature.targets.some((t) => t.status === 'requested') && (
            <p className="mt-1 text-xs text-gray-500">
              Approve or reject in <Link to="/approvals" className="text-primary hover:underline">Pending Approvals</Link>.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Docs link (overrides the one found)</label>
          <div className="flex gap-2">
            <Input value={docsLink} onChange={(e) => setDocsLink(e.target.value)} placeholder="https://docs.databricks.com/…" className="h-9" />
            <Button size="sm" variant="outline" disabled={saving} onClick={() => save({ docs_link: docsLink })}>Save</Button>
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Verification probe (REST GET path)</label>
          <div className="flex gap-2">
            <Input value={probePath} onChange={(e) => setProbePath(e.target.value)} placeholder="/api/2.0/…" className="h-9" />
            <Button size="sm" variant="outline" disabled={saving} onClick={() => save({ probe_path: probePath })}>Save</Button>
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Scope</label>
          <div className="flex gap-2">
            {(['workspace', 'account'] as const).map((s) => (
              <Button
                key={s}
                size="sm"
                variant={feature.scope === s ? 'default' : 'outline'}
                disabled={saving || feature.scope === s || (s === 'workspace' && !feature.setting_name)}
                onClick={() => save({ scope: s })}
              >
                {s === 'workspace' ? 'Workspace' : 'Account'}
              </Button>
            ))}
          </div>
        </div>
      </div>
      {error && <p className="rounded bg-red-50 p-2 text-red-700">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type View = 'workspace' | 'account';

export function PreviewFeatures() {
  const [data, setData] = useState<PreviewFeaturesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>('workspace');
  const [search, setSearch] = useState('');
  const [phase, setPhase] = useState('');
  const [status, setStatus] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ feature: PreviewFeature; action: 'enable' | 'disable' } | null>(null);
  const [approving, setApproving] = useState<PreviewFeature | null>(null);
  const [message, setMessage] = useState<{ text: string; requestId?: string } | null>(null);
  // Captured once per visit, so "New" marks what arrived since the previous visit.
  const [lastVisit] = useState<string | null>(() => localStorage.getItem(LAST_VISIT_KEY));

  const load = useCallback(async () => {
    try {
      setData(await api.getPreviewFeatures());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load preview features');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString());
  }, [load]);

  const syncing = !!data?.sync.last_run.running;
  // While a sync runs, poll until it finishes, then reload the list.
  useVisibleInterval(async () => {
    const s = await api.getPreviewSyncStatus().catch(() => null);
    if (s && !s.last_run.running) load();
    else if (s) setData((d) => (d ? { ...d, sync: s } : d));
  }, 3000, syncing);

  const startSync = async () => {
    try {
      const s = await api.startPreviewSync();
      setData((d) => (d ? { ...d, sync: s } : d));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start sync');
    }
  };

  const isNew = (f: PreviewFeature) =>
    !!lastVisit && parseUtc(f.first_seen_at).getTime() > new Date(lastVisit).getTime();

  const features = useMemo(() => data?.features ?? [], [data]);
  const workspaces = useMemo(() => data?.workspaces ?? [], [data]);
  const byView = useMemo(
    () => ({
      workspace: features.filter((f) => f.scope !== 'account'),
      account: features.filter((f) => f.scope === 'account'),
    }),
    [features],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return byView[view].filter((f) => {
      if (phase && f.phase !== phase) return false;
      if (q && ![f.display_name, f.setting_name, f.description].some((v) => (v || '').toLowerCase().includes(q))) {
        return false;
      }
      const tm = targetMap(f);
      if (view === 'workspace' && workspace && !tm[workspace]?.available) return false;
      if (status) {
        const chips = view === 'account'
          ? [chipFor(tm[ACCOUNT_TARGET])]
          : (workspace ? [workspace] : workspaces.map((w) => w.name)).map((n) => chipFor(tm[n]));
        if (!chips.some((c) => c.key === status)) return false;
      }
      return true;
    });
  }, [byView, view, search, phase, status, workspace, workspaces]);

  const shownWorkspaces = workspace ? workspaces.filter((w) => w.name === workspace) : workspaces;
  const lastRun = data?.sync.last_run;
  const failedWorkspaces = (lastRun?.workspaces || []).filter((w) => !w.ok);
  const newCount = (v: View) => byView[v].filter(isNew).length;

  const replaceFeature = (f: PreviewFeature) =>
    setData((d) => (d ? { ...d, features: d.features.map((x) => (x.id === f.id ? { ...x, ...f } : x)) } : d));

  const canDisable = (f: PreviewFeature) => {
    const tm = targetMap(f);
    if (f.scope === 'account') return !ineligibleReason(tm[ACCOUNT_TARGET], 'disable');
    return workspaces.some((w) => !ineligibleReason(tm[w.name], 'disable'));
  };
  const canEnable = (f: PreviewFeature) => {
    const tm = targetMap(f);
    if (f.scope === 'account') {
      return !ineligibleReason(tm[ACCOUNT_TARGET] ?? { target: ACCOUNT_TARGET, available: true, status: 'not_requested', drift: false }, 'enable');
    }
    return workspaces.some((w) => !ineligibleReason(tm[w.name], 'enable'));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-xl font-bold text-gray-900">
            <FlaskConical className="h-5 w-5" /> Preview Features
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Databricks Beta and Public Preview features across your target workspaces. Request one to start an
            approval; approved workspace previews are turned on automatically and checked afterwards.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end">
          <Button onClick={startSync} disabled={syncing} variant="outline" className="gap-2">
            <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? 'Syncing…' : 'Sync now'}
          </Button>
          <p className="mt-1 text-xs text-gray-500">
            Last synced {ago(lastRun?.finished_at || data?.sync.last_seen_at)}
            {data?.sync.next_run ? ` · next ${ago(data.sync.next_run)}` : data && !data.sync.cron ? ' · no schedule' : ''}
          </p>
        </div>
      </div>

      {message && (
        <div className="flex items-center justify-between rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          <span>
            {message.text}{' '}
            {message.requestId && (
              <Link to={`/requests/${message.requestId}`} className="font-medium underline">View request</Link>
            )}
          </span>
          <button onClick={() => setMessage(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
        </div>
      )}
      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
      {lastRun?.message && !lastRun.running && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">Last sync failed: {lastRun.message}</div>
      )}
      {failedWorkspaces.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            Couldn’t read previews from {failedWorkspaces.map((w) => w.name).join(', ')} on the last sync; their
            statuses may be out of date.
            {failedWorkspaces.map((w) => (
              <div key={w.name} className="mt-1 text-xs text-amber-800">{w.name}: {w.message}</div>
            ))}
          </div>
        </div>
      )}

      <div className="flex gap-2 border-b border-gray-200">
        {(['workspace', 'account'] as const).map((v) => (
          <button
            key={v}
            onClick={() => { setView(v); setExpanded(null); setStatus(''); }}
            className={`flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors ${view === v ? 'border-b-2 border-primary text-primary' : 'text-gray-600 hover:text-gray-900'}`}
          >
            {v === 'workspace' ? 'Workspace previews' : 'Account previews'}
            <span className="rounded-full bg-gray-100 px-2 text-xs text-gray-600">{byView[v].length}</span>
            {newCount(v) > 0 && (
              <span className="rounded-full bg-primary px-2 text-xs text-white">{newCount(v)} new</span>
            )}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search previews" className="h-9 pl-8" />
        </div>
        <select value={phase} onChange={(e) => setPhase(e.target.value)} className="h-9 rounded-md border border-gray-300 px-2 text-sm">
          <option value="">All phases</option>
          <option value="BETA">Beta</option>
          <option value="PUBLIC_PREVIEW">Public Preview</option>
          <option value="PRIVATE_PREVIEW">Private Preview</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-gray-300 px-2 text-sm">
          <option value="">Any status</option>
          <option value="not_requested">Not requested</option>
          <option value="already_on">Already on</option>
          <option value="requested">Requested</option>
          <option value="approved">Approved</option>
          <option value="implemented">Implemented</option>
          <option value="drift">Drift</option>
          <option value="rejected">Rejected</option>
          {view === 'workspace' && <option value="not_available">Not available</option>}
        </select>
        {view === 'workspace' && (
          <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} className="h-9 rounded-md border border-gray-300 px-2 text-sm">
            <option value="">All workspaces</option>
            {workspaces.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
          </select>
        )}
        <span className="text-xs text-gray-500">{visible.length} shown</span>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex items-center justify-center p-10 text-gray-500">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading previews…
            </div>
          ) : visible.length === 0 ? (
            <div className="p-10 text-center text-sm text-gray-500">
              {features.length === 0
                ? 'No previews yet. Click Sync now to read them from your target workspaces.'
                : 'No previews match these filters.'}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="w-8 px-2 py-2" />
                    <th className="px-3 py-2 font-medium">Feature</th>
                    {view === 'workspace'
                      ? shownWorkspaces.map((w) => <th key={w.name} className="px-3 py-2 font-medium">{w.name}</th>)
                      : <th className="px-3 py-2 font-medium">Account status</th>}
                    <th className="px-3 py-2 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((f) => {
                    const tm = targetMap(f);
                    const open = expanded === f.id;
                    const acct = tm[ACCOUNT_TARGET];
                    const listed = acct?.observed_value?.workspaces_listed;
                    return (
                      <FeatureRows
                        key={f.id}
                        open={open}
                        onToggle={() => setExpanded(open ? null : f.id)}
                        colSpan={3 + (view === 'workspace' ? shownWorkspaces.length : 1)}
                        details={<FeatureDetails feature={f} onSaved={replaceFeature} />}
                      >
                        <td className="px-3 py-2 align-top">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-gray-900">{f.display_name}</span>
                            <PhaseChip phase={f.phase} />
                            {isNew(f) && <span className="rounded bg-primary px-1.5 py-0.5 text-[11px] text-white">New</span>}
                          </div>
                          <p className="mt-0.5 text-xs text-gray-500">{firstLine(f.description || f.announcement_text)}</p>
                        </td>
                        {view === 'workspace' ? (
                          shownWorkspaces.map((w) => (
                            <td key={w.name} className="px-3 py-2 align-top"><StatusChip chip={chipFor(tm[w.name])} /></td>
                          ))
                        ) : (
                          <td className="px-3 py-2 align-top">
                            <StatusChip chip={chipFor(acct ?? { target: ACCOUNT_TARGET, available: true, status: 'not_requested', drift: false })} />
                            {listed ? (
                              <div className="mt-1 text-xs text-gray-500">
                                On in {acct?.observed_value?.workspaces_on ?? 0} of {listed} workspaces
                              </div>
                            ) : (
                              <div className="mt-1 text-xs text-gray-400">Not readable from a workspace</div>
                            )}
                          </td>
                        )}
                        <td className="whitespace-nowrap px-3 py-2 align-top">
                          <div className="flex justify-end gap-2">
                            {approvableRequests(f).length > 0 && (
                              <Button size="sm" className={`${ACTION_WIDTH} bg-green-700 hover:bg-green-800`} onClick={() => setApproving(f)}>
                                Approve
                              </Button>
                            )}
                            {/* A greyed-out Request only shows when it's the row's only action. */}
                            {(canEnable(f) || (!canDisable(f) && approvableRequests(f).length === 0)) && (
                              <Button size="sm" className={ACTION_WIDTH} disabled={!canEnable(f)} onClick={() => setDialog({ feature: f, action: 'enable' })}>
                                Request
                              </Button>
                            )}
                            {canDisable(f) && (
                              <Button size="sm" variant="outline" className={ACTION_WIDTH} onClick={() => setDialog({ feature: f, action: 'disable' })}>
                                Request turn off
                              </Button>
                            )}
                          </div>
                        </td>
                      </FeatureRows>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {approving && (
        <ApproveDialog
          feature={approving}
          onClose={() => setApproving(null)}
          onDone={(m) => {
            setApproving(null);
            setMessage(m);
            load();
          }}
        />
      )}
      {dialog && (
        <RequestDialog
          feature={dialog.feature}
          action={dialog.action}
          workspaces={workspaces}
          onClose={() => setDialog(null)}
          onDone={(m) => {
            setDialog(null);
            setMessage(m);
            load();
          }}
        />
      )}
    </div>
  );
}

function FeatureRows({
  open,
  onToggle,
  colSpan,
  details,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  colSpan: number;
  details: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <>
      <tr className="border-b hover:bg-gray-50/50">
        <td className="px-2 py-2 align-top">
          <button onClick={onToggle} className="text-gray-400 hover:text-gray-700" aria-label={open ? 'Collapse' : 'Expand'}>
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </td>
        {children}
      </tr>
      {open && (
        <tr className="border-b">
          <td colSpan={colSpan} className="p-0">{details}</td>
        </tr>
      )}
    </>
  );
}
