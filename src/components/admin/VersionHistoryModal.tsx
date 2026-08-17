import { useEffect, useState } from 'react';
import { History, Loader2, RotateCcw, Workflow as WorkflowIcon, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { Workflow, WorkflowVersion } from '../../services/api';

interface Props {
  workflowId: string;
  currentVersion: number;
  onRestored: (workflow: Workflow) => void;
  onClose: () => void;
  /** When true, authoring is locked in this env: show history read-only (no Restore). */
  locked?: boolean;
}

/** Snapshot history with one-click restore. Restoring loads the chosen snapshot's
 *  body back as a draft so it can be reviewed/tested before re-publishing.
 *
 *  Two kinds of snapshot appear here: published versions, and the autosave backups
 *  taken right before something overwrote a draft (e.g. an authoring-assistant
 *  save) — which is how draft edits that used to be unrecoverable get recovered. */
export function VersionHistoryModal({ workflowId, currentVersion, onRestored, onClose, locked }: Props) {
  const [versions, setVersions] = useState<WorkflowVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listWorkflowVersions(workflowId)
      .then((v) => !cancelled && setVersions(v))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load history'));
    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const restore = async (snapshot: WorkflowVersion) => {
    const isAutosave = snapshot.kind === 'autosave';
    const label = isAutosave
      ? `the autosave from ${snapshot.published_at ? new Date(snapshot.published_at).toLocaleString() : 'earlier'}`
      : `version ${snapshot.version}`;
    if (
      !confirm(
        `Restore ${label} as a new draft? Current unpublished edits will be replaced ` +
          '(they get backed up here first, so this is reversible).',
      )
    )
      return;
    setRestoring(snapshot.id);
    setError(null);
    try {
      // Autosaves share a version number, so they can only be addressed by id.
      const workflow = await api.rollbackWorkflow(
        workflowId,
        isAutosave ? { snapshotId: snapshot.id } : { version: snapshot.version },
      );
      onRestored(workflow);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to restore');
    } finally {
      setRestoring(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <History className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Version history</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 overflow-y-auto">
          {error && (
            <div className="mb-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}
          {versions === null ? (
            <div className="flex items-center justify-center py-10 text-gray-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : versions.length === 0 ? (
            <div className="text-sm text-gray-400 text-center py-10">
              No snapshots yet. Publishing captures a version here, and a backup is
              taken automatically before the assistant overwrites a draft.
            </div>
          ) : (
            <div className="space-y-2">
              {versions.map((v) => {
                const isAutosave = v.kind === 'autosave';
                return (
                  <div
                    key={v.id}
                    className={`flex items-center gap-3 border rounded-md px-3 py-2 ${
                      isAutosave ? 'border-dashed border-gray-200 bg-gray-50/60' : 'border-gray-200'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">
                          {isAutosave ? 'Autosave' : `v${v.version}`}
                        </span>
                        {isAutosave ? (
                          <span className="text-[10px] bg-amber-50 text-amber-700 rounded px-1.5 py-0.5">
                            backup · based on v{v.version}
                          </span>
                        ) : (
                          v.version === currentVersion && (
                            <span className="text-[10px] bg-green-50 text-green-700 rounded px-1.5 py-0.5">
                              current
                            </span>
                          )
                        )}
                        {v.has_graph && (
                          <span className="text-[10px] text-gray-500 inline-flex items-center gap-1">
                            <WorkflowIcon className="w-3 h-3" /> {v.stage_count} stage{v.stage_count === 1 ? '' : 's'}
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-gray-400 truncate">
                        {v.published_at ? new Date(v.published_at).toLocaleString() : ''}
                        {v.published_by ? ` · ${v.published_by}` : ''}
                        {v.note ? ` · ${v.note}` : ''}
                      </div>
                    </div>
                    {!locked && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={restoring !== null}
                        onClick={() => restore(v)}
                      >
                        {restoring === v.id ? (
                          <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                        ) : (
                          <RotateCcw className="w-3.5 h-3.5 mr-1" />
                        )}
                        Restore
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default VersionHistoryModal;
