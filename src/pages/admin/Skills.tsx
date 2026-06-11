import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  Wand2,
  Plus,
  Trash2,
  Loader2,
  Save,
  Search,
  CheckCircle2,
  Circle,
  Send,
  Undo2,
  FileText,
  Workflow,
  Copy,
  History,
  Download,
  Upload,
} from 'lucide-react';
import { api } from '../../services/api';
import type { Skill, SkillInput, WorkflowGraphSpec, WorkflowTool } from '../../services/api';
import { WorkflowEditor } from '../../components/admin/WorkflowEditor';
import { PublishConfirmModal } from '../../components/admin/PublishConfirmModal';
import { VersionHistoryModal } from '../../components/admin/VersionHistoryModal';
import { ImportSkillsModal } from '../../components/admin/ImportSkillsModal';

const inputClass =
  'w-full border border-gray-300 rounded-md h-10 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent';
const textareaClass =
  'w-full border border-gray-300 rounded-md p-3 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-accent';

interface SkillFormState {
  id: string | null;
  key: string;
  name: string;
  goal: string;
  instructions_markdown: string;
  allowed_tools: string;
  policy_ref: string;
  request_type: string;
  status: string;
  graph_spec: WorkflowGraphSpec | null;
}

const emptyForm: SkillFormState = {
  id: null,
  key: '',
  name: '',
  goal: '',
  instructions_markdown: '',
  allowed_tools: '',
  policy_ref: '',
  request_type: '',
  status: 'draft',
  graph_spec: null,
};

const splitList = (value: string): string[] =>
  value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);

function toForm(skill: Skill): SkillFormState {
  return {
    id: skill.id,
    key: skill.key,
    name: skill.name || '',
    goal: skill.goal || '',
    instructions_markdown: skill.instructions_markdown || '',
    allowed_tools: (skill.allowed_tools || []).join(', '),
    policy_ref: skill.policy_ref || '',
    request_type: skill.request_type || '',
    status: skill.status,
    graph_spec: skill.graph_spec ?? null,
  };
}

export function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [form, setForm] = useState<SkillFormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // The selected skill id and active tab live in the URL so the browser Back
  // button steps through skills (and back to the list) instead of leaving the page.
  const skillParam = searchParams.get('skill');
  const tab: 'details' | 'workflow' =
    searchParams.get('tab') === 'workflow' ? 'workflow' : 'details';
  const loadedIdRef = useRef<string | null>(null);
  const [tools, setTools] = useState<WorkflowTool[]>([]);
  const [baseline, setBaseline] = useState<string>(() => JSON.stringify(emptyForm));
  const [publishTarget, setPublishTarget] = useState<Skill | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showImport, setShowImport] = useState(false);

  const dirty = useMemo(() => JSON.stringify(form) !== baseline, [form, baseline]);

  const setFormBaselined = (next: SkillFormState) => {
    setForm(next);
    setBaseline(JSON.stringify(next));
  };

  const setTab = (next: 'details' | 'workflow') => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        params.set('tab', next);
        return params;
      },
      { replace: true },
    );
  };

  const setSkillParam = (id: string | null, opts: { replace?: boolean } = {}) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (id) params.set('skill', id);
        else params.delete('skill');
        return params;
      },
      opts,
    );
  };

  const loadList = async () => {
    setLoading(true);
    setError(null);
    try {
      setSkills(await api.listSkills(true));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadList();
    api
      .listWorkflowTools()
      .then(setTools)
      .catch(() => setTools([]));
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // Load the skill named in the URL (deep links + Back/Forward navigation).
  useEffect(() => {
    if (skillParam === loadedIdRef.current) return;
    // Guard unsaved edits before swapping skills (covers Back/Forward too).
    if (!confirmDiscard()) {
      setSkillParam(loadedIdRef.current, { replace: true });
      return;
    }
    if (!skillParam) {
      loadedIdRef.current = null;
      setFormBaselined(emptyForm);
      return;
    }
    loadedIdRef.current = skillParam;
    api
      .getSkill(skillParam)
      .then((full) => setFormBaselined(toForm(full)))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load skill'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skillParam]);

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return skills;
    return skills.filter(
      (s) =>
        s.key.toLowerCase().includes(f) ||
        (s.name || '').toLowerCase().includes(f) ||
        (s.goal || '').toLowerCase().includes(f),
    );
  }, [skills, filter]);

  const selectedSkill = useMemo(
    () => skills.find((s) => s.id === form.id) ?? null,
    [skills, form.id],
  );

  const confirmDiscard = () =>
    !dirty || confirm('You have unsaved changes. Discard them?');

  const selectSkill = (skill: Skill) => {
    if (skill.id === form.id) return;
    // Push a history entry so Back returns to the list, then let the URL effect
    // load it (and prompt about unsaved edits in one place).
    setSkillParam(skill.id);
  };

  const startNew = () => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.delete('skill');
      params.set('tab', 'details');
      return params;
    });
  };

  const buildInput = (): SkillInput => ({
    key: form.key.trim(),
    name: form.name.trim() || form.key.trim(),
    goal: form.goal.trim() || null,
    instructions_markdown: form.instructions_markdown,
    allowed_tools: form.allowed_tools.trim() ? splitList(form.allowed_tools) : null,
    policy_ref: form.policy_ref.trim() || null,
    request_type: form.request_type.trim() || null,
    status: form.status,
    graph_spec: form.graph_spec,
  });

  const save = async () => {
    if (!form.key.trim()) {
      setError('Key is required');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const wasNew = !form.id;
      const saved = form.id
        ? await api.updateSkill(form.id, buildInput())
        : await api.createSkill(buildInput());
      loadedIdRef.current = saved.id;
      setFormBaselined(toForm(saved));
      if (wasNew) setSkillParam(saved.id, { replace: true });
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save skill');
    } finally {
      setSaving(false);
    }
  };

  const togglePublish = async (skill: Skill) => {
    setBusyId(skill.id);
    setError(null);
    try {
      const updated =
        skill.status === 'published'
          ? await api.unpublishSkill(skill.id)
          : await api.publishSkill(skill.id);
      if (form.id === skill.id) setFormBaselined(toForm(updated));
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change status');
    } finally {
      setBusyId(null);
    }
  };

  const clone = async (skill: Skill) => {
    setBusyId(skill.id);
    setError(null);
    try {
      const created = await api.cloneSkill(skill.id);
      await loadList();
      const full = await api.getSkill(created.id);
      loadedIdRef.current = created.id;
      setFormBaselined(toForm(full));
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        params.set('skill', created.id);
        params.set('tab', 'details');
        return params;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to duplicate skill');
    } finally {
      setBusyId(null);
    }
  };

  const exportBundle = async (opts: { ids?: string[]; publishedOnly?: boolean } = {}) => {
    setError(null);
    try {
      const bundle = await api.exportSkillsBundle(opts);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `atlas-skills-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to export skills');
    }
  };

  const remove = async (skill: Skill) => {
    if (!confirm(`Delete skill "${skill.key}"? This cannot be undone.`)) return;
    setBusyId(skill.id);
    setError(null);
    try {
      await api.deleteSkill(skill.id);
      if (form.id === skill.id) {
        loadedIdRef.current = null;
        setFormBaselined(emptyForm);
        setSkillParam(null, { replace: true });
      }
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete skill');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-heading flex items-center gap-2">
            <Wand2 className="w-6 h-6 text-accent" />
            Skills
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-3xl">
            Author the agent's capabilities as data. Each published skill appears in the
            agent's capabilities list and is returned by <code>get_workflow_instructions</code>.
            Edit and publish here instead of changing instruction files and redeploying.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => exportBundle()} variant="outline" title="Export all skills as a portable bundle">
            <Download className="w-4 h-4 mr-1" /> Export
          </Button>
          <Button onClick={() => setShowImport(true)} variant="outline" title="Import a bundle from another environment">
            <Upload className="w-4 h-4 mr-1" /> Import
          </Button>
          <Button onClick={startNew} variant="outline">
            <Plus className="w-4 h-4 mr-1" /> New skill
          </Button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-md px-4 py-2 text-sm">
          {error}
        </div>
      )}

      <div className={`grid grid-cols-1 gap-6 ${tab === 'workflow' ? '' : 'lg:grid-cols-3'}`}>
        {/* List */}
        <Card className={tab === 'workflow' ? 'hidden' : 'lg:col-span-1'}>
          <CardHeader>
            <CardTitle className="text-base">All skills</CardTitle>
            <CardDescription>{skills.length} total</CardDescription>
            <div className="relative mt-2">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className={`${inputClass} pl-9`}
                placeholder="Filter skills..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </div>
          </CardHeader>
          <CardContent className="max-h-[60vh] overflow-y-auto space-y-1">
            {loading ? (
              <div className="flex items-center justify-center py-8 text-gray-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ) : (
              filtered.map((s) => (
                <div
                  key={s.id}
                  className={`flex items-center justify-between gap-2 rounded-md px-2 py-2 cursor-pointer hover:bg-gray-50 ${
                    form.id === s.id ? 'bg-accent/10' : ''
                  }`}
                  onClick={() => selectSkill(s)}
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{s.key}</div>
                    <div className="text-xs text-gray-500 truncate">{s.goal || '—'}</div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {s.status === 'published' ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                        <CheckCircle2 className="w-3 h-3" /> live
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[11px] text-gray-400">
                        <Circle className="w-3 h-3" /> draft
                      </span>
                    )}
                    <button
                      type="button"
                      title="Duplicate"
                      className="text-gray-300 hover:text-accent p-0.5"
                      disabled={busyId === s.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        clone(s);
                      }}
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))
            )}
            {!loading && filtered.length === 0 && (
              <div className="text-sm text-gray-400 py-6 text-center">No skills found.</div>
            )}
          </CardContent>
        </Card>

        {/* Editor */}
        <Card className={tab === 'workflow' ? '' : 'lg:col-span-2'}>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="text-base">
                  {form.id ? `Edit: ${form.key}` : 'New skill'}
                </CardTitle>
                <CardDescription>
                  {form.id ? (
                    <span className="inline-flex items-center gap-2">
                      <span
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] ${
                          form.status === 'published'
                            ? 'bg-green-50 text-green-700'
                            : 'bg-gray-100 text-gray-500'
                        }`}
                      >
                        {form.status === 'published' ? 'live' : 'draft'}
                      </span>
                      {selectedSkill && <span>v{selectedSkill.version}</span>}
                      {selectedSkill?.updated_at && (
                        <span className="text-gray-400">
                          · edited {new Date(selectedSkill.updated_at).toLocaleDateString()}
                          {selectedSkill.created_by ? ` by ${selectedSkill.created_by}` : ''}
                        </span>
                      )}
                    </span>
                  ) : (
                    'Define a new agent capability.'
                  )}
                </CardDescription>
              </div>
              {form.id && (
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowHistory(true)}
                    title="View version history"
                  >
                    <History className="w-4 h-4 mr-1" /> History
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => form.id && exportBundle({ ids: [form.id] })}
                    title="Export this skill"
                  >
                    <Download className="w-4 h-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busyId === form.id}
                    onClick={() => {
                      const s = skills.find((x) => x.id === form.id);
                      if (s) clone(s);
                    }}
                    title="Duplicate as a new draft"
                  >
                    <Copy className="w-4 h-4 mr-1" /> Duplicate
                  </Button>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-1 border-b border-gray-200 -mt-2">
              <button
                type="button"
                onClick={() => setTab('details')}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                  tab === 'details'
                    ? 'border-accent text-accent font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <FileText className="w-4 h-4" /> Details & instructions
              </button>
              <button
                type="button"
                onClick={() => setTab('workflow')}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                  tab === 'workflow'
                    ? 'border-accent text-accent font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <Workflow className="w-4 h-4" /> Workflow graph
                {form.graph_spec && form.graph_spec.stages.length > 0 && (
                  <span className="ml-1 text-[10px] bg-accent/10 text-accent rounded-full px-1.5 py-0.5">
                    {form.graph_spec.stages.length}
                  </span>
                )}
              </button>
            </div>

            {tab === 'details' && (
            <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Key (internal name) *
                </label>
                <input
                  className={inputClass}
                  placeholder="e.g. workspace_access"
                  value={form.key}
                  disabled={!!form.id}
                  onChange={(e) => setForm({ ...form, key: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Name</label>
                <input
                  className={inputClass}
                  placeholder="Human-readable name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Goal (one-line capability description)
              </label>
              <input
                className={inputClass}
                placeholder="Request access to an existing Databricks workspace."
                value={form.goal}
                onChange={(e) => setForm({ ...form, goal: e.target.value })}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Policy ref
                </label>
                <input
                  className={inputClass}
                  placeholder="data.agent.tools"
                  value={form.policy_ref}
                  onChange={(e) => setForm({ ...form, policy_ref: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Request type
                </label>
                <input
                  className={inputClass}
                  placeholder="workspace_access"
                  value={form.request_type}
                  onChange={(e) => setForm({ ...form, request_type: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Status</label>
                <select
                  className={inputClass}
                  value={form.status}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}
                >
                  <option value="draft">draft</option>
                  <option value="published">published</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Allowed tools (capability scoping, comma-separated; blank = defaults)
              </label>
              <input
                className={inputClass}
                placeholder="get_target_workspaces, execute_workflow"
                value={form.allowed_tools}
                onChange={(e) => setForm({ ...form, allowed_tools: e.target.value })}
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Instructions (markdown)
              </label>
              <textarea
                className={textareaClass}
                rows={16}
                placeholder="# Workflow Instructions&#10;&#10;**Goal**: ...&#10;&#10;## Information to Gather&#10;..."
                value={form.instructions_markdown}
                onChange={(e) =>
                  setForm({ ...form, instructions_markdown: e.target.value })
                }
              />
            </div>
            </>
            )}

            {tab === 'workflow' && (
              <div>
                {form.graph_spec ? (
                  <WorkflowEditor
                    spec={form.graph_spec}
                    tools={tools}
                    onChange={(graph_spec) => setForm({ ...form, graph_spec })}
                  />
                ) : (
                  <div className="text-center py-10 border border-dashed border-gray-200 rounded-lg">
                    <Workflow className="w-8 h-8 text-gray-300 mx-auto mb-3" />
                    <p className="text-sm text-gray-600 mb-1">No workflow graph yet</p>
                    <p className="text-xs text-gray-400 max-w-md mx-auto mb-4">
                      Build an executable workflow as ordered approval gates and provisioning
                      steps. When published, the durable executor runs this graph instead of the
                      code catalog.
                    </p>
                    <Button
                      variant="outline"
                      onClick={() =>
                        setForm({
                          ...form,
                          graph_spec: {
                            name: form.key.trim() || form.request_type.trim() || 'workflow',
                            completed_status: 'completed',
                            complete_fact: null,
                            stages: [],
                          },
                        })
                      }
                    >
                      <Plus className="w-4 h-4 mr-1" /> Start a workflow
                    </Button>
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center gap-2 pt-2">
              <Button onClick={save} disabled={saving || !dirty}>
                {saving ? (
                  <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                ) : (
                  <Save className="w-4 h-4 mr-1" />
                )}
                Save
              </Button>
              {dirty && (
                <span className="text-xs text-amber-600 inline-flex items-center gap-1">
                  Unsaved changes
                </span>
              )}
              {form.id && (
                <>
                  <Button
                    variant="outline"
                    disabled={busyId === form.id}
                    onClick={() => {
                      const s = skills.find((x) => x.id === form.id);
                      if (!s) return;
                      if (s.status === 'published') {
                        togglePublish(s);
                        return;
                      }
                      if (dirty) {
                        setError('Save your changes before publishing.');
                        return;
                      }
                      setError(null);
                      setPublishTarget(s);
                    }}
                  >
                    {form.status === 'published' ? (
                      <>
                        <Undo2 className="w-4 h-4 mr-1" /> Unpublish
                      </>
                    ) : (
                      <>
                        <Send className="w-4 h-4 mr-1" /> Publish
                      </>
                    )}
                  </Button>
                  <Button
                    variant="outline"
                    className="text-red-600 hover:bg-red-50"
                    disabled={busyId === form.id}
                    onClick={() => {
                      const s = skills.find((x) => x.id === form.id);
                      if (s) remove(s);
                    }}
                  >
                    <Trash2 className="w-4 h-4 mr-1" /> Delete
                  </Button>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {publishTarget && (
        <PublishConfirmModal
          skill={publishTarget}
          graphSpec={form.graph_spec}
          tools={tools}
          onClose={() => setPublishTarget(null)}
          onConfirm={async () => {
            await togglePublish(publishTarget);
            setPublishTarget(null);
          }}
        />
      )}

      {showHistory && form.id && (
        <VersionHistoryModal
          skillId={form.id}
          currentVersion={selectedSkill?.version ?? 0}
          onClose={() => setShowHistory(false)}
          onRestored={async (skill) => {
            setFormBaselined(toForm(skill));
            setShowHistory(false);
            await loadList();
          }}
        />
      )}

      {showImport && (
        <ImportSkillsModal
          onClose={() => setShowImport(false)}
          onImported={() => loadList()}
        />
      )}
    </div>
  );
}

export default Skills;
