import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileText,
  Loader2,
  PencilLine,
  Sparkles,
  UserPlus,
  Wand2,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { ContextDocument, ContextReview, ContextReviewRecommendation } from '../../services/api';
import { METRIC_LABELS, gradeStyle } from '../../lib/contextReview';
import { collapseUnchanged, diffLines } from '../../lib/lineDiff';
import { renderMarkdownSafe } from '../../lib/markdown';
import { errorMessage } from '../../lib/errors';

const PRIORITY_STYLE: Record<ContextReviewRecommendation['priority'], string> = {
  high: 'bg-red-50 text-red-700',
  medium: 'bg-amber-50 text-amber-700',
  low: 'bg-gray-100 text-gray-600',
};

const SOURCE_LABEL: Record<string, string> = {
  confluence: 'Confluence',
  catalog: 'Catalog',
  domain: 'Domain',
  document_history: 'Document history',
  other: 'Other',
};

const RELATION_LABEL: Record<string, string> = {
  source: 'source',
  overlaps: 'overlaps',
  conflicts: 'conflicts',
  newer: 'newer version',
  related: 'related',
};

const POLL_MS = 4000;

function ago(iso: string | null): string {
  if (!iso) return '';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString();
}

interface Props {
  documentId: string;
  canEdit: boolean;
  /** The editor has unsaved changes. */
  isDirty: boolean;
  onApplied: (doc: ContextDocument) => void;
  onSetOwner: (email: string) => Promise<void>;
  onUseInEditor: (proposal: { title?: string; body?: string }) => void;
  onOpenDocument: (domainId: string, documentId: string) => void;
  /** A review started or finished, so list badges can refresh. */
  onReviewChanged: () => void;
}

/** AI review of one document: grades, fixes, owner suggestions and a proposed rewrite. */
export function ContextReviewPanel({
  documentId,
  canEdit,
  isDirty,
  onApplied,
  onSetOwner,
  onUseInEditor,
  onOpenDocument,
  onReviewChanged,
}: Props) {
  const [review, setReview] = useState<ContextReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'start' | 'apply' | 'owner' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const lastStatus = useRef<string | null>(null);

  const load = async () => {
    try {
      const { review: r } = await api.getContextDocumentReview(documentId);
      setReview(r);
      if (lastStatus.current && r && lastStatus.current !== r.status && !['queued', 'running'].includes(r.status)) {
        onReviewChanged();
      }
      lastStatus.current = r?.status ?? null;
    } catch (e) {
      setError(errorMessage(e, 'Failed to load the review'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setReview(null);
    setLoading(true);
    setError(null);
    setNotice(null);
    lastStatus.current = null;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  const inFlight = review?.status === 'queued' || review?.status === 'running';
  useEffect(() => {
    if (!inFlight) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inFlight, documentId]);

  const start = async () => {
    setBusy('start');
    setError(null);
    setNotice(null);
    try {
      const { review: r } = await api.reviewContextDocument(documentId);
      setReview(r);
      lastStatus.current = r.status;
      onReviewChanged();
    } catch (e) {
      setError(errorMessage(e, 'Failed to start the review'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="border border-gray-200 rounded-lg">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-100 bg-gray-50 rounded-t-lg">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="w-4 h-4 text-accent shrink-0" />
          <span className="text-sm font-semibold text-gray-800">AI review</span>
          {review?.status === 'completed' && review.grade && (
            <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${gradeStyle(review.grade)}`}>
              {review.grade} · {review.overall_score}/100
            </span>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={start}
          disabled={busy !== null || inFlight || loading}
          title="Grade this document for someone new to the platform, using the catalog and Confluence"
        >
          {busy === 'start' || inFlight ? (
            <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
          ) : (
            <Sparkles className="w-3.5 h-3.5 mr-1" />
          )}
          {inFlight ? 'Reviewing…' : review ? 'Review again' : 'Review'}
        </Button>
      </div>

      <div className="p-3 space-y-3 text-sm">
        {error && <Banner tone="error" text={error} />}
        {notice && <Banner tone="success" text={notice} />}

        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
          </div>
        ) : !review ? (
          <p className="text-xs text-gray-500">
            Grade this document the way someone new to the platform would read it: completeness, clarity,
            accuracy against Confluence and the rest of the catalog, actionability and findability. You get
            specific fixes, owner suggestions if it has no owner, and a cleaned-up rewrite. Nothing changes until
            you apply it.
          </p>
        ) : inFlight ? (
          <p className="text-xs text-gray-500 flex items-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            {review.status === 'queued'
              ? 'Waiting for its turn…'
              : 'Reading the catalog and Confluence for this document. This usually takes a minute or two.'}
          </p>
        ) : review.status === 'failed' ? (
          <Banner tone="error" text={review.error || 'The review failed.'} />
        ) : (
          <ReviewBody
            review={review}
            canEdit={canEdit}
            isDirty={isDirty}
            busy={busy}
            setBusy={setBusy}
            setError={setError}
            setNotice={setNotice}
            onReplaced={setReview}
            onApplied={onApplied}
            onSetOwner={onSetOwner}
            onUseInEditor={onUseInEditor}
            onOpenDocument={onOpenDocument}
          />
        )}
      </div>
    </div>
  );
}

function ReviewBody({
  review,
  canEdit,
  isDirty,
  busy,
  setBusy,
  setError,
  setNotice,
  onReplaced,
  onApplied,
  onSetOwner,
  onUseInEditor,
  onOpenDocument,
}: {
  review: ContextReview;
  canEdit: boolean;
  isDirty: boolean;
  busy: 'start' | 'apply' | 'owner' | null;
  setBusy: (b: 'start' | 'apply' | 'owner' | null) => void;
  setError: (e: string | null) => void;
  setNotice: (n: string | null) => void;
  onReplaced: (r: ContextReview) => void;
  onApplied: (doc: ContextDocument) => void;
  onSetOwner: (email: string) => Promise<void>;
  onUseInEditor: (proposal: { title?: string; body?: string }) => void;
  onOpenDocument: (domainId: string, documentId: string) => void;
}) {
  const hasBody = !!review.proposed_body_markdown;
  const hasTitle = !!review.proposed_title;
  const hasTags = !!review.proposed_tags?.length;
  const [parts, setParts] = useState({ title: hasTitle, body: hasBody, tags: hasTags });
  const [diffMode, setDiffMode] = useState<'changes' | 'full' | 'preview'>('changes');

  useEffect(() => {
    setParts({ title: hasTitle, body: hasBody, tags: hasTags });
  }, [review.id, hasTitle, hasBody, hasTags]);

  const lines = useMemo(() => {
    if (!hasBody) return [];
    const all = diffLines(review.original_body_markdown || '', review.proposed_body_markdown || '');
    return diffMode === 'changes' ? collapseUnchanged(all) : all;
  }, [review.original_body_markdown, review.proposed_body_markdown, hasBody, diffMode]);

  const counts = useMemo(() => {
    if (!hasBody) return { add: 0, del: 0 };
    const all = diffLines(review.original_body_markdown || '', review.proposed_body_markdown || '');
    return {
      add: all.filter((l) => l.kind === 'add').length,
      del: all.filter((l) => l.kind === 'del').length,
    };
  }, [review.original_body_markdown, review.proposed_body_markdown, hasBody]);

  const apply = async () => {
    if (isDirty && !window.confirm('You have unsaved edits in the editor. Applying replaces them. Continue?')) return;
    setBusy('apply');
    setError(null);
    setNotice(null);
    try {
      const res = await api.applyContextReview(review.id, parts);
      if (res.review) onReplaced(res.review);
      onApplied(res.document);
      setNotice('Suggestion applied and saved.');
    } catch (e) {
      setError(errorMessage(e, 'Failed to apply the suggestion'));
    } finally {
      setBusy(null);
    }
  };

  const setOwner = async (email: string) => {
    setBusy('owner');
    setError(null);
    setNotice(null);
    try {
      await onSetOwner(email);
      setNotice(`Owner set to ${email}.`);
    } catch (e) {
      setError(errorMessage(e, 'Failed to set the owner'));
    } finally {
      setBusy(null);
    }
  };

  const anyPart = (parts.title && hasTitle) || (parts.body && hasBody) || (parts.tags && hasTags);
  const blockedReason = !canEdit
    ? 'Editing is off in this environment; change the document through Git instead.'
    : review.is_outdated
    ? 'The document changed after this review. Review it again to apply a fresh suggestion.'
    : review.applied_at
    ? 'Already applied.'
    : '';

  return (
    <>
      {review.summary && <p className="text-gray-700">{review.summary}</p>}
      <p className="text-[11px] text-gray-400">
        Reviewed {ago(review.finished_at)}
        {review.requested_by ? ` for ${review.requested_by}` : ''}
        {review.model ? ` · ${review.model}` : ''} · {review.tool_calls} lookup{review.tool_calls === 1 ? '' : 's'}
      </p>
      {review.is_outdated && (
        <Banner tone="warn" text="The document changed after this review, so the grades and rewrite may be out of date." />
      )}
      {review.forced && (
        <Banner tone="warn" text="The reviewer ran out of tool rounds or time, so it may not have checked everything." />
      )}
      {review.confluence_status && review.confluence_status !== 'available' && review.confluence_note && (
        <Banner tone="info" text={review.confluence_note} />
      )}

      <Section title="Scores">
        <div className="space-y-1.5">
          {review.scores.map((s) => (
            <div key={s.metric} className="grid grid-cols-[8.5rem_4.5rem_1fr] gap-2 items-start text-xs">
              <span className="font-medium text-gray-700">{METRIC_LABELS[s.metric] ?? s.metric}</span>
              <span className="flex gap-0.5 pt-1" aria-label={`${s.score} out of 5`}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <span
                    key={n}
                    className={`h-1.5 w-2.5 rounded-sm ${
                      n <= s.score ? (s.score >= 4 ? 'bg-green-500' : s.score === 3 ? 'bg-amber-500' : 'bg-red-500') : 'bg-gray-200'
                    }`}
                  />
                ))}
              </span>
              <span className="text-gray-500">{s.rationale}</span>
            </div>
          ))}
        </div>
      </Section>

      {review.recommendations.length > 0 && (
        <Section title={`Recommendations (${review.recommendations.length})`}>
          <ul className="space-y-2">
            {review.recommendations.map((r, i) => (
              <li key={i} className="border border-gray-100 rounded-md p-2 text-xs space-y-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className={`uppercase tracking-wide text-[10px] px-1.5 py-0.5 rounded ${PRIORITY_STYLE[r.priority]}`}>
                    {r.priority}
                  </span>
                  <span className="text-gray-400">{METRIC_LABELS[r.metric] ?? r.metric}</span>
                </div>
                <p className="text-gray-800">{r.issue}</p>
                {r.excerpt && (
                  <blockquote
                    className="border-l-2 border-gray-300 pl-2 text-gray-500 italic whitespace-pre-wrap"
                    title={r.excerpt_found ? 'Quoted from the document' : 'Not found word for word in the document'}
                  >
                    {r.excerpt}
                    {!r.excerpt_found && <span className="not-italic text-gray-400"> (paraphrased)</span>}
                  </blockquote>
                )}
                <p className="text-gray-700 whitespace-pre-wrap">
                  <span className="font-medium">Suggestion: </span>
                  {r.suggestion}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {review.suggested_owners.length > 0 && (
        <Section title="Suggested owners">
          <ul className="space-y-1.5">
            {review.suggested_owners.map((o, i) => (
              <li key={i} className="flex items-start justify-between gap-2 text-xs">
                <div className="min-w-0">
                  <p className="text-gray-800">
                    <span className="font-medium">{o.name}</span>
                    {o.email && o.email !== o.name && <span className="text-gray-500"> · {o.email}</span>}
                    <span className="text-gray-400">
                      {' '}
                      · {SOURCE_LABEL[o.source] ?? o.source} · {Math.round(o.confidence * 100)}%
                    </span>
                  </p>
                  <p className="text-gray-500">{o.reason}</p>
                </div>
                {o.email ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 shrink-0"
                    disabled={!canEdit || busy !== null}
                    onClick={() => setOwner(o.email as string)}
                    title={canEdit ? 'Make this person the document owner' : 'Editing is off in this environment'}
                  >
                    {busy === 'owner' ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <UserPlus className="w-3 h-3 mr-1" />}
                    Set as owner
                  </Button>
                ) : (
                  <span className="text-[11px] text-gray-400 shrink-0" title="The reviewer didn't see this person's email">
                    no email found
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {review.related_sources.length > 0 && (
        <Section title="Sources checked">
          <ul className="space-y-1 text-xs">
            {review.related_sources.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5">
                {s.kind === 'catalog' ? (
                  <FileText className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />
                ) : (
                  <ExternalLink className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />
                )}
                <span className="min-w-0">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                      {s.title}
                    </a>
                  ) : s.document_id && s.domain_id ? (
                    <button
                      className="text-accent hover:underline text-left"
                      onClick={() => onOpenDocument(s.domain_id as string, s.document_id as string)}
                    >
                      {s.title}
                    </button>
                  ) : (
                    <span className="text-gray-700">{s.title}</span>
                  )}
                  <span
                    className={`ml-1.5 text-[10px] uppercase tracking-wide ${
                      s.relation === 'conflicts' || s.relation === 'newer' ? 'text-orange-600' : 'text-gray-400'
                    }`}
                  >
                    {RELATION_LABEL[s.relation] ?? s.relation}
                  </span>
                  {s.note && <span className="block text-gray-500">{s.note}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(hasBody || hasTitle || hasTags) && (
        <Section title="Proposed changes">
          {review.rewrite_summary && (
            <p className="text-xs text-gray-600 whitespace-pre-wrap mb-2">{review.rewrite_summary}</p>
          )}
          <div className="space-y-1.5 text-xs">
            {hasTitle && (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={parts.title}
                  onChange={(e) => setParts({ ...parts, title: e.target.checked })}
                />
                <span>
                  <span className="font-medium">Title: </span>
                  <span className="line-through text-red-600">{review.original_title}</span>
                  <ArrowRight className="inline w-3 h-3 mx-1 text-gray-400" />
                  <span className="text-green-700">{review.proposed_title}</span>
                </span>
              </label>
            )}
            {hasTags && (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={parts.tags}
                  onChange={(e) => setParts({ ...parts, tags: e.target.checked })}
                />
                <span>
                  <span className="font-medium">Tags: </span>
                  <span className="text-gray-500">{(review.original_tags || []).join(', ') || 'none'}</span>
                  <ArrowRight className="inline w-3 h-3 mx-1 text-gray-400" />
                  <span className="text-green-700">{(review.proposed_tags || []).join(', ')}</span>
                </span>
              </label>
            )}
            {hasBody && (
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={parts.body}
                  onChange={(e) => setParts({ ...parts, body: e.target.checked })}
                />
                <span>
                  <span className="font-medium">Body: </span>
                  <span className="text-green-700">+{counts.add}</span>{' '}
                  <span className="text-red-600">−{counts.del}</span> lines
                </span>
              </label>
            )}
          </div>

          {hasBody && (
            <div className="mt-2">
              <div className="flex gap-1 mb-1">
                {(['changes', 'full', 'preview'] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setDiffMode(m)}
                    className={`text-[11px] px-2 py-0.5 rounded ${
                      diffMode === m ? 'bg-accent text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    {m === 'changes' ? 'Changes' : m === 'full' ? 'Full diff' : 'Preview'}
                  </button>
                ))}
              </div>
              {diffMode === 'preview' ? (
                <div
                  className="border border-gray-200 rounded-md p-3 max-h-96 overflow-auto prose prose-sm agent-prose max-w-none"
                  dangerouslySetInnerHTML={{ __html: renderMarkdownSafe(review.proposed_body_markdown || '') }}
                />
              ) : (
                <div className="border border-gray-200 rounded-md max-h-96 overflow-auto font-mono text-[11px] leading-5">
                  {lines.map((line, i) =>
                    line.kind === 'skip' ? (
                      <div key={i} className="px-2 bg-gray-50 text-gray-400 select-none">
                        ⋯ {line.count} unchanged line{line.count === 1 ? '' : 's'}
                      </div>
                    ) : (
                      <div
                        key={i}
                        className={`px-2 whitespace-pre-wrap break-words ${
                          line.kind === 'add'
                            ? 'bg-green-50 text-green-800'
                            : line.kind === 'del'
                            ? 'bg-red-50 text-red-700 line-through decoration-red-300'
                            : 'text-gray-600'
                        }`}
                      >
                        <span className="select-none text-gray-400 mr-2">
                          {line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}
                        </span>
                        {line.text || ' '}
                      </div>
                    ),
                  )}
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 flex-wrap mt-2">
            <Button
              size="sm"
              onClick={apply}
              disabled={!!blockedReason || !anyPart || busy !== null}
              title={blockedReason || 'Save the selected changes to the document'}
            >
              {busy === 'apply' ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Wand2 className="w-3.5 h-3.5 mr-1" />}
              Apply selected
            </Button>
            {canEdit && (hasBody || hasTitle) && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() =>
                  onUseInEditor({
                    title: parts.title && hasTitle ? (review.proposed_title as string) : undefined,
                    body: parts.body && hasBody ? (review.proposed_body_markdown as string) : undefined,
                  })
                }
                title="Copy the selected changes into the editor above without saving, so you can adjust them first"
              >
                <PencilLine className="w-3.5 h-3.5 mr-1" /> Edit before saving
              </Button>
            )}
            {review.applied_at && (
              <span className="text-[11px] text-green-700 inline-flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> Applied {ago(review.applied_at)}
                {review.applied_by ? ` by ${review.applied_by}` : ''}
              </span>
            )}
            {blockedReason && !review.applied_at && (
              <span className="text-[11px] text-gray-500">{blockedReason}</span>
            )}
          </div>
        </Section>
      )}
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1.5">{title}</p>
      {children}
    </div>
  );
}

function Banner({ tone, text }: { tone: 'error' | 'success' | 'warn' | 'info'; text: string }) {
  const style =
    tone === 'error'
      ? 'text-red-600 bg-red-50 border-red-200'
      : tone === 'success'
      ? 'text-green-700 bg-green-50 border-green-200'
      : tone === 'warn'
      ? 'text-amber-800 bg-amber-50 border-amber-200'
      : 'text-gray-600 bg-gray-50 border-gray-200';
  const Icon = tone === 'success' ? CheckCircle2 : AlertTriangle;
  return (
    <div className={`text-xs border rounded-md px-3 py-2 flex items-start gap-2 ${style}`}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" /> <span>{text}</span>
    </div>
  );
}

export default ContextReviewPanel;
