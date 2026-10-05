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
  Plus,
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
// Beyond this many workspaces the table shows one summary column instead of one column each.
const MAX_WORKSPACE_COLUMNS = 6;

const PHASE_LABELS: Record<string, string> = {
  BETA: 'Beta',
  PUBLIC_PREVIEW: 'Public Preview',
  PRIVATE_PREVIEW: 'Private Preview',
  GA: 'GA',
};

const PHASE_DOTS: Record<string, string> = {
  BETA: 'bg-purple-500',
  PUBLIC_PREVIEW: 'bg-sky-500',
  PRIVATE_PREVIEW: 'bg-amber-500',
  GA: 'bg-gray-400',
};

// Summary order: statuses that need attention first.
const ROLLUP: Array<[key: string, label: string]> = [
  ['drift', 'Drift ⚠'],
  ['requested', 'Requested'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
  ['implemented', 'Implemented'],
  ['already_on', 'Already on'],
  ['unreadable', 'Unreadable'],
  ['not_requested', 'Not requested'],
  ['not_available', 'Not available'],
];
const rollupRank = (key: string) => ROLLUP.findIndex(([k]) => k === key);

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
  // A request carried over from a hand-added feature can cover a workspace that doesn't list the setting yet.
  if (t.target !== ACCOUNT_TARGET && !t.available && !['requested', 'approved', 'implemented'].includes(t.status)) {
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

function PhaseLabel({ phase }: { phase?: string | null }) {
  if (!phase) return <span className="text-gray-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-gray-700">
      <span className={`h-2 w-2 rounded-full ${PHASE_DOTS[phase] || PHASE_DOTS.GA}`} />
      {PHASE_LABELS[phase] || phase}
    </span>
  );
}

const listNames = (names: string[], max = 10): string =>
  names.length > max ? `${names.slice(0, max).join(', ')} and ${names.length - max} more` : names.join(', ');

/** Counts of each status across many workspaces, in place of one column per workspace. */
function StatusRollup({ feature, workspaces }: { feature: PreviewFeature; workspaces: TargetWorkspace[] }) {
  const tm = targetMap(feature);
  const groups = new Map<string, { tone: ChipTone; names: string[] }>();
  for (const w of workspaces) {
    const chip = chipFor(tm[w.name]);
    const g = groups.get(chip.key);
    if (g) g.names.push(w.name);
    else groups.set(chip.key, { tone: chip.tone, names: [w.name] });
  }
  return (
    <div className="flex flex-wrap gap-1">
      {ROLLUP.filter(([key]) => groups.has(key)).map(([key, label]) => {
        const g = groups.get(key)!;
        return (
          <span
            key={key}
            title={listNames(g.names)}
            className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs ${TONE_STYLES[g.tone]}`}
          >
            <span className="font-semibold">{g.names.length}</span> {label}
          </span>
        );
      })}
    </div>
  );
}

/** Per-workspace statuses for one feature, searchable, for when the table only shows a summary. */
function WorkspaceBreakdown({ feature, workspaces }: { feature: PreviewFeature; workspaces: TargetWorkspace[] }) {
  const [query, setQuery] = useState('');
  const tm = targetMap(feature);
  const q = query.trim().toLowerCase();
  const rows = workspaces
    .filter((w) => !q || w.name.toLowerCase().includes(q) || w.environment.toLowerCase().includes(q))
    .map((w) => ({ ws: w, chip: chipFor(tm[w.name]) }))
    .sort((a, b) => rollupRank(a.chip.key) - rollupRank(b.chip.key) || a.ws.name.localeCompare(b.ws.name));

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Workspaces ({q ? `${rows.length} of ${workspaces.length}` : workspaces.length})
        </div>
        <div className="relative w-56">
          <Search className="absolute left-2 top-2 h-3.5 w-3.5 text-gray-400" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a workspace" className="h-7 pl-7 text-xs" />
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto rounded border border-gray-200 bg-white">
        {rows.length === 0 ? (
          <p className="p-3 text-xs text-gray-500">No workspaces match.</p>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3">
            {rows.map(({ ws, chip }) => (
              <div key={ws.name} className="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-1.5">
                <span className="min-w-0 truncate" title={ws.host}>
                  <span className="font-medium text-gray-800">{ws.name}</span>{' '}
                  <span className="text-xs text-gray-400">{ws.environment}</span>
                </span>
                <StatusChip chip={chip} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
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
  const [query, setQuery] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = query.trim().toLowerCase();
  const shownOptions = q
    ? options.filter((o) => o.ws.name.toLowerCase().includes(q) || o.ws.environment.toLowerCase().includes(q))
    : options;
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
                <span className="text-sm font-medium text-gray-700">
                  Workspaces
                  {selected.length > 0 && <span className="ml-2 text-xs font-normal text-gray-500">{selected.length} selected</span>}
                </span>
                <div className="flex gap-3">
                  {selected.length > 0 && (
                    <button type="button" className="text-xs text-gray-500 hover:underline" onClick={() => setSelected([])}>
                      Clear
                    </button>
                  )}
                  <button
                    type="button"
                    className="text-xs text-primary hover:underline"
                    onClick={() =>
                      setSelected((cur) => [
                        ...new Set([...cur, ...shownOptions.filter((o) => !o.reason).map((o) => o.ws.name)]),
                      ])
                    }
                  >
                    {q ? 'Select matching eligible' : 'Select all eligible'}
                  </button>
                </div>
              </div>
              {options.length > MAX_WORKSPACE_COLUMNS && (
                <div className="relative mb-2">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={`Filter ${options.length} workspaces by name or environment`}
                    className="h-9 pl-8"
                  />
                </div>
              )}
              <div className="max-h-56 space-y-1 overflow-y-auto rounded border p-2">
                {options.length === 0 && <p className="text-sm text-gray-500">No target workspaces are configured.</p>}
                {options.length > 0 && shownOptions.length === 0 && <p className="text-sm text-gray-500">No workspaces match.</p>}
                {shownOptions.map(({ ws, reason }) => (
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
// Features added by hand: add and match dialogs
// ---------------------------------------------------------------------------

const isManual = (f: PreviewFeature) => f.origin === 'manual';
const CARRIED_STATUSES = ['requested', 'approved', 'implemented'];
const MANUAL_PHASES = ['PRIVATE_PREVIEW', 'BETA', 'PUBLIC_PREVIEW'] as const;
type ManualPhase = (typeof MANUAL_PHASES)[number];

function DialogShell({ title, subtitle, onClose, children, footer }: {
  title: string;
  subtitle: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  useEscape(onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
            <p className="mt-1 text-sm text-gray-500">{subtitle}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4 overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex justify-end gap-2 border-t px-5 py-3">{footer}</div>
      </div>
    </div>
  );
}

function AddFeatureDialog({ defaultScope, onClose, onDone }: {
  defaultScope: 'workspace' | 'account';
  onClose: () => void;
  onDone: (f: PreviewFeature) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scope, setScope] = useState(defaultScope);
  const [phase, setPhase] = useState<ManualPhase>('PRIVATE_PREVIEW');
  const [docsLink, setDocsLink] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      onDone(await api.createManualPreviewFeature({
        display_name: name.trim(),
        description: description.trim() || undefined,
        scope,
        phase,
        docs_link: docsLink.trim() || undefined,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add the feature');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogShell
      title="Add a preview by hand"
      subtitle="For a preview Databricks hasn’t announced or listed yet, such as one your account team is enabling for you. It’s requested and approved like any other; a person makes the change. If its setting shows up later, use Match to switch over to it."
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={submitting || !name.trim()}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Add feature
          </Button>
        </>
      }
    >
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Name</label>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="What Databricks calls it, as far as you know" autoFocus />
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">What it does</label>
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="Approvers see this. Include who told you about it and any requirements."
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <span className="mb-1 block text-sm font-medium text-gray-700">Turned on for</span>
          <div className="flex gap-2">
            {(['workspace', 'account'] as const).map((s) => (
              <Button key={s} size="sm" variant={scope === s ? 'default' : 'outline'} onClick={() => setScope(s)}>
                {s === 'workspace' ? 'Each workspace' : 'The account'}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Phase</label>
          <select value={phase} onChange={(e) => setPhase(e.target.value as ManualPhase)} className="h-9 w-full rounded-md border border-gray-300 px-2 text-sm">
            {MANUAL_PHASES.map((p) => <option key={p} value={p}>{PHASE_LABELS[p]}</option>)}
          </select>
        </div>
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Docs link (optional)</label>
        <Input value={docsLink} onChange={(e) => setDocsLink(e.target.value)} placeholder="https://…" />
      </div>
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
    </DialogShell>
  );
}

function MatchDialog({ feature, features, onClose, onDone }: {
  feature: PreviewFeature;
  features: PreviewFeature[];
  onClose: () => void;
  onDone: (matched: PreviewFeature, message: { text: string; requestId?: string }) => void;
}) {
  const suggestions = feature.match_suggestions ?? [];
  const [query, setQuery] = useState('');
  const [onlyNew, setOnlyNew] = useState(suggestions.length === 0);
  const [selected, setSelected] = useState<string | null>(suggestions[0]?.id ?? null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pool = useMemo(() => features.filter((f) => !isManual(f) && !f.archived_at), [features]);
  const addedAt = parseUtc(feature.first_seen_at).getTime();
  const isNewer = (f: PreviewFeature) => parseUtc(f.first_seen_at).getTime() >= addedAt;
  const suggestedIds = new Set(suggestions.map((s) => s.id));
  const q = query.trim().toLowerCase();
  const list = q || onlyNew
    ? pool
        .filter((f) => !q || [f.display_name, f.setting_name, f.description].some((v) => (v || '').toLowerCase().includes(q)))
        .filter((f) => !onlyNew || isNewer(f))
        .sort((a, b) => Number(suggestedIds.has(b.id)) - Number(suggestedIds.has(a.id))
          || parseUtc(b.first_seen_at).getTime() - parseUtc(a.first_seen_at).getTime())
        .slice(0, 50)
    : suggestions.map((s) => pool.find((f) => f.id === s.id)).filter((f): f is PreviewFeature => !!f);

  const carried = feature.targets.filter((t) => CARRIED_STATUSES.includes(t.status));
  const chosen = pool.find((f) => f.id === selected);
  const scopeMismatch = !!chosen && carried.length > 0 && (chosen.scope === 'account') !== (feature.scope === 'account');

  const submit = async () => {
    if (!chosen) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.matchPreviewFeature(feature.id, chosen.id);
      onDone(res.feature, {
        text: `${feature.display_name} now uses ${chosen.display_name}. ${res.moved_targets.length
          ? 'Its requests and statuses moved over, and the real setting is checked from now on.'
          : 'Nothing had been requested yet, so only the entry was replaced.'}`,
        requestId: res.request_ids.length === 1 ? res.request_ids[0] : undefined,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Match failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogShell
      title={`Match: ${feature.display_name}`}
      subtitle="Pick the feature Databricks now lists for this. Open requests, approvals and implemented statuses move to it, so the setting can be switched and checked automatically from then on. The hand-added entry is then removed."
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={submitting || !chosen || scopeMismatch}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Match and replace
          </Button>
        </>
      }
    >
      <p className="rounded bg-gray-50 p-2 text-sm text-gray-600">
        {carried.length
          ? `Moves over: ${carried.map((t) => `${t.target === ACCOUNT_TARGET ? 'Account' : t.target} (${chipFor(t).label.toLowerCase()})`).join(', ')}.`
          : 'Nothing has been requested for this yet, so only the entry is replaced.'}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search synced previews" className="h-9 pl-8" />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />
          New since this was added
        </label>
      </div>
      <div className="max-h-72 space-y-1 overflow-y-auto rounded border p-2">
        {list.length === 0 && (
          <p className="p-2 text-sm text-gray-500">
            {q || onlyNew ? 'No synced previews match.' : 'No likely matches yet. Search, or tick “New since this was added”.'}
          </p>
        )}
        {list.map((f) => (
          <label
            key={f.id}
            className={`flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm ${selected === f.id ? 'bg-primary/5' : 'hover:bg-gray-50'}`}
          >
            <input type="radio" name="match" className="mt-1" checked={selected === f.id} onChange={() => setSelected(f.id)} />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-gray-900">{f.display_name}</span>
                {suggestedIds.has(f.id) && <span className="rounded bg-amber-100 px-1.5 text-[11px] text-amber-800">Likely match</span>}
              </span>
              <span className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
                {f.setting_name && <code>{f.setting_name}</code>}
                <PhaseLabel phase={f.phase} />
                <span>{f.scope === 'account' ? 'Account' : 'Workspace'}</span>
                <span>first seen {ago(f.first_seen_at)}</span>
              </span>
            </span>
          </label>
        ))}
      </div>
      {scopeMismatch && (
        <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">
          This one is {feature.scope === 'account' ? 'account' : 'workspace'}-level but {chosen?.display_name} is{' '}
          {chosen?.scope === 'account' ? 'account' : 'workspace'}-level. Change the scope of one so they agree, then match.
        </p>
      )}
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}
    </DialogShell>
  );
}

function ManualFeatureEditor({ feature, saving, onSave, onRemove }: {
  feature: PreviewFeature;
  saving: boolean;
  onSave: (body: { display_name: string; description: string; phase: string }) => void;
  onRemove: () => void;
}) {
  const [name, setName] = useState(feature.display_name);
  const [description, setDescription] = useState(feature.description || '');
  const [phase, setPhase] = useState<string>(feature.phase || 'PRIVATE_PREVIEW');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const inFlight = feature.targets.some((t) => t.status === 'requested' || t.status === 'approved');
  const dirty = name !== feature.display_name || description !== (feature.description || '') || phase !== feature.phase;

  return (
    <div className="rounded border border-indigo-100 bg-white p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Added by hand</div>
      <div className="grid gap-3 md:grid-cols-[2fr_1fr]">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="h-9" aria-label="Name" />
        <select value={phase} onChange={(e) => setPhase(e.target.value)} className="h-9 rounded-md border border-gray-300 px-2 text-sm" aria-label="Phase">
          {MANUAL_PHASES.map((p) => <option key={p} value={p}>{PHASE_LABELS[p]}</option>)}
        </select>
      </div>
      <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-2" aria-label="What it does" />
      <div className="mt-2 flex items-center justify-between gap-2">
        <Button size="sm" variant="outline" disabled={saving || !dirty || !name.trim()} onClick={() => onSave({ display_name: name, description, phase })}>
          Save
        </Button>
        {confirmRemove ? (
          <span className="flex items-center gap-2 text-xs text-gray-600">
            Remove it from the list?
            <Button size="sm" variant="outline" onClick={() => setConfirmRemove(false)}>Keep</Button>
            <Button size="sm" className="bg-red-600 hover:bg-red-700" disabled={saving} onClick={onRemove}>Remove</Button>
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={saving || inFlight}
            title={inFlight ? 'Finish or reject the open request first' : undefined}
            onClick={() => setConfirmRemove(true)}
          >
            Remove from list
          </Button>
        )}
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
  breakdown,
  onSaved,
  onRemoved,
}: {
  feature: PreviewFeature;
  breakdown?: TargetWorkspace[];
  onSaved: (f: PreviewFeature) => void;
  onRemoved: (f: PreviewFeature) => void;
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

  const remove = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.removePreviewFeature(feature.id);
      onRemoved(feature);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Remove failed');
      setSaving(false);
    }
  };

  const manual = isManual(feature);
  const withRequests = feature.targets.filter((t) => t.request_id || t.note || t.observe_error);

  return (
    <div className="space-y-4 bg-gray-50/60 px-4 py-4 text-sm">
      <div className="space-y-2">
        {manual && (
          <p className="rounded border border-indigo-100 bg-indigo-50 p-2 text-indigo-900">
            Added by hand{feature.created_by ? ` by ${feature.created_by}` : ''} because Databricks doesn’t list it yet.
            Requests go to a person to arrange with Databricks. When its setting shows up in a workspace, use{' '}
            <strong>Match</strong> to switch over to it.
          </p>
        )}
        {!!feature.matched_from?.length && (
          <p className="text-xs text-gray-500">Replaces the hand-added {feature.matched_from.join(', ')}.</p>
        )}
        {feature.description && !manual && <p className="whitespace-pre-line text-gray-800">{feature.description}</p>}
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
          <span>Scope: {feature.scope} ({manual ? 'chosen when added' : feature.scope_source === 'admin' ? 'set by an admin' : feature.scope_source === 'inferred' ? 'from the release notes' : 'listed by the workspace'})</span>
          {feature.value_type && feature.value_type !== 'boolean' && feature.setting_name && (
            <span className="text-amber-700">Not an on/off setting: changes are made by hand</span>
          )}
        </div>
      </div>

      {manual && <ManualFeatureEditor feature={feature} saving={saving} onSave={save} onRemove={remove} />}

      {breakdown && <WorkspaceBreakdown feature={feature} workspaces={breakdown} />}

      {withRequests.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Requests and notes</div>
          <div className="max-h-64 overflow-y-auto">
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
          </div>
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
                disabled={saving || feature.scope === s || (s === 'workspace' && !feature.setting_name && !manual)}
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
  const [view, setView] = useState<View>('account');
  const [search, setSearch] = useState('');
  const [phase, setPhase] = useState('');
  const [status, setStatus] = useState('');
  const [wsQuery, setWsQuery] = useState('');
  const [environment, setEnvironment] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ feature: PreviewFeature; action: 'enable' | 'disable' } | null>(null);
  const [approving, setApproving] = useState<PreviewFeature | null>(null);
  const [adding, setAdding] = useState(false);
  const [matching, setMatching] = useState<PreviewFeature | null>(null);
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

  const environments = useMemo(() => [...new Set(workspaces.map((w) => w.environment))].sort(), [workspaces]);
  const workspaceFiltered = !!(wsQuery.trim() || environment);
  // An exact name picks that one workspace; otherwise the text matches any part of the name or environment.
  const shownWorkspaces = useMemo(() => {
    const q = wsQuery.trim().toLowerCase();
    const inEnv = environment ? workspaces.filter((w) => w.environment === environment) : workspaces;
    if (!q) return inEnv;
    const exact = inEnv.filter((w) => w.name.toLowerCase() === q);
    return exact.length ? exact : inEnv.filter((w) => w.name.toLowerCase().includes(q) || w.environment.toLowerCase().includes(q));
  }, [workspaces, wsQuery, environment]);
  const summarize = shownWorkspaces.length > MAX_WORKSPACE_COLUMNS;

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return byView[view].filter((f) => {
      if (phase && f.phase !== phase) return false;
      if (q && ![f.display_name, f.setting_name, f.description].some((v) => (v || '').toLowerCase().includes(q))) {
        return false;
      }
      const tm = targetMap(f);
      if (view === 'workspace' && workspaceFiltered && !shownWorkspaces.some((w) => tm[w.name]?.available)) return false;
      if (status) {
        const chips = view === 'account'
          ? [chipFor(tm[ACCOUNT_TARGET])]
          : shownWorkspaces.map((w) => chipFor(tm[w.name]));
        if (!chips.some((c) => c.key === status)) return false;
      }
      return true;
    });
  }, [byView, view, search, phase, status, workspaceFiltered, shownWorkspaces]);

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
          <div className="flex gap-2">
            <Button onClick={() => setAdding(true)} variant="outline" className="gap-2" title="Add a preview Databricks doesn’t list yet">
              <Plus className="h-4 w-4" /> Add feature
            </Button>
            <Button onClick={startSync} disabled={syncing} variant="outline" className="gap-2">
              <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? 'Syncing…' : 'Sync now'}
            </Button>
          </div>
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
          <div className="min-w-0">
            Couldn’t read previews from {listNames(failedWorkspaces.map((w) => w.name), 5)} on the last sync; their
            statuses may be out of date.
            <details className="mt-1 text-xs text-amber-800">
              <summary className="cursor-pointer">Show errors</summary>
              <div className="mt-1 max-h-40 overflow-y-auto">
                {failedWorkspaces.map((w) => (
                  <div key={w.name} className="mt-1">{w.name}: {w.message}</div>
                ))}
              </div>
            </details>
          </div>
        </div>
      )}

      <div className="flex gap-2 border-b border-gray-200">
        {(['account', 'workspace'] as const).map((v) => (
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
        {view === 'workspace' && environments.length > 1 && (
          <select value={environment} onChange={(e) => setEnvironment(e.target.value)} className="h-9 rounded-md border border-gray-300 px-2 text-sm">
            <option value="">All environments</option>
            {environments.map((env) => <option key={env} value={env}>{env}</option>)}
          </select>
        )}
        {view === 'workspace' && workspaces.length > 1 && (
          <div className="relative w-60">
            <Input
              value={wsQuery}
              onChange={(e) => setWsQuery(e.target.value)}
              list="preview-workspace-names"
              placeholder={`Filter ${workspaces.length} workspaces`}
              className="h-9 pr-7"
            />
            <datalist id="preview-workspace-names">
              {workspaces.map((w) => <option key={w.name} value={w.name} />)}
            </datalist>
            {wsQuery && (
              <button onClick={() => setWsQuery('')} className="absolute right-2 top-2.5 text-gray-400 hover:text-gray-600" aria-label="Clear workspace filter">
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
        <span className="text-xs text-gray-500">
          {visible.length} shown
          {view === 'workspace' && workspaceFiltered && ` · ${shownWorkspaces.length} of ${workspaces.length} workspaces`}
        </span>
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
                    <th className="px-3 py-2 font-medium">Phase</th>
                    {view === 'account' ? (
                      <th className="px-3 py-2 font-medium">Account status</th>
                    ) : summarize ? (
                      <th className="px-3 py-2 font-medium" title="Expand a row to see each workspace">
                        Status · {shownWorkspaces.length} workspaces
                      </th>
                    ) : (
                      shownWorkspaces.map((w) => (
                        <th key={w.name} className="px-3 py-2 font-medium" title={`${w.environment} · ${w.host}`}>{w.name}</th>
                      ))
                    )}
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
                        colSpan={4 + (view === 'workspace' && !summarize ? shownWorkspaces.length : 1)}
                        details={
                          <FeatureDetails
                            feature={f}
                            breakdown={view === 'workspace' && summarize ? shownWorkspaces : undefined}
                            onSaved={replaceFeature}
                            onRemoved={(removed) => {
                              setExpanded(null);
                              setMessage({ text: `Removed ${removed.display_name} from the list.` });
                              load();
                            }}
                          />
                        }
                      >
                        <td className="px-3 py-2 align-top">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-gray-900">{f.display_name}</span>
                            {isManual(f) && (
                              <span className="rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-700" title="Databricks doesn’t list this yet">
                                Added by hand
                              </span>
                            )}
                            {isManual(f) && f.match_suggestions?.some((s) => s.new_since_added) && (
                              <button
                                onClick={() => setMatching(f)}
                                className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800 hover:bg-amber-100"
                                title={`Possibly: ${f.match_suggestions.filter((s) => s.new_since_added).map((s) => s.display_name).join(', ')}`}
                              >
                                Setting may have appeared
                              </button>
                            )}
                            {isNew(f) && <span className="rounded bg-primary px-1.5 py-0.5 text-[11px] text-white">New</span>}
                          </div>
                          <p className="mt-0.5 text-xs text-gray-500">{firstLine(f.description || f.announcement_text)}</p>
                        </td>
                        <td className="px-3 py-2 align-top"><PhaseLabel phase={f.phase} /></td>
                        {view === 'workspace' && summarize ? (
                          <td className="px-3 py-2 align-top"><StatusRollup feature={f} workspaces={shownWorkspaces} /></td>
                        ) : view === 'workspace' ? (
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
                              <div className="mt-1 text-xs text-gray-400">
                                {isManual(f) ? 'Confirmed by the implementer' : 'Not readable from a workspace'}
                              </div>
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
                            {isManual(f) && (
                              <Button
                                size="sm"
                                variant="outline"
                                className={ACTION_WIDTH}
                                title="Replace this with the setting Databricks now lists"
                                onClick={() => setMatching(f)}
                              >
                                Match
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

      {adding && (
        <AddFeatureDialog
          defaultScope={view}
          onClose={() => setAdding(false)}
          onDone={(f) => {
            setAdding(false);
            setView(f.scope === 'account' ? 'account' : 'workspace');
            setSearch('');
            setPhase('');
            setStatus('');
            setExpanded(f.id);
            setMessage({ text: `Added ${f.display_name}. Request it like any other preview; when Databricks starts listing its setting, use Match to switch over.` });
            load();
          }}
        />
      )}
      {matching && (
        <MatchDialog
          feature={matching}
          features={features}
          onClose={() => setMatching(null)}
          onDone={(matched, m) => {
            setMatching(null);
            setView(matched.scope === 'account' ? 'account' : 'workspace');
            setExpanded(matched.id);
            setMessage(m);
            load();
          }}
        />
      )}
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
