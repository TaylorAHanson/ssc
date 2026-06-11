import { useEffect, useState } from 'react';
import { History, Loader2, RotateCcw, Workflow, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { Skill, SkillVersion } from '../../services/api';

interface Props {
  skillId: string;
  currentVersion: number;
  onRestored: (skill: Skill) => void;
  onClose: () => void;
}

/** Published-version history with one-click rollback. Restoring loads the chosen
 *  version's body back as a draft so it can be reviewed/tested before re-publishing. */
export function VersionHistoryModal({ skillId, currentVersion, onRestored, onClose }: Props) {
  const [versions, setVersions] = useState<SkillVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listSkillVersions(skillId)
      .then((v) => !cancelled && setVersions(v))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load history'));
    return () => {
      cancelled = true;
    };
  }, [skillId]);

  const restore = async (version: number) => {
    if (!confirm(`Restore version ${version} as a new draft? Current unpublished edits will be replaced.`)) return;
    setRestoring(version);
    setError(null);
    try {
      const skill = await api.rollbackSkill(skillId, version);
      onRestored(skill);
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
              No published versions yet. Publishing a skill snapshots it here.
            </div>
          ) : (
            <div className="space-y-2">
              {versions.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center gap-3 border border-gray-200 rounded-md px-3 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">v{v.version}</span>
                      {v.version === currentVersion && (
                        <span className="text-[10px] bg-green-50 text-green-700 rounded px-1.5 py-0.5">
                          current
                        </span>
                      )}
                      {v.has_graph && (
                        <span className="text-[10px] text-gray-500 inline-flex items-center gap-1">
                          <Workflow className="w-3 h-3" /> {v.stage_count} stage{v.stage_count === 1 ? '' : 's'}
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-gray-400 truncate">
                      {v.published_at ? new Date(v.published_at).toLocaleString() : ''}
                      {v.published_by ? ` · ${v.published_by}` : ''}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={restoring !== null}
                    onClick={() => restore(v.version)}
                  >
                    {restoring === v.version ? (
                      <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                    ) : (
                      <RotateCcw className="w-3.5 h-3.5 mr-1" />
                    )}
                    Restore
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default VersionHistoryModal;
