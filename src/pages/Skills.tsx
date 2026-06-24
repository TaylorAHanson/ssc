import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import {
  Plus, Trash2, Save, Sparkles, RefreshCw, FolderTree, User as UserIcon,
  Loader2, BookOpen,
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { cn } from '../lib/utils';
import {
  listSkills, listSkillLocations, getSkill, createSkill, updateSkill, deleteSkill,
  type Skill, type SkillLocation,
} from '../services/api';
import { ChatView } from '../components/chat/ChatView';

const SKILL_TOOLS = new Set(['save_skill', 'delete_skill', 'get_skill', 'list_skills']);

/** Strip a leading `---` YAML frontmatter block, returning just the body. */
function stripFrontmatter(content: string): string {
  const m = /^\s*---\n[\s\S]*?\n---\n?/.exec(content || '');
  return m ? (content.slice(m[0].length).replace(/^\n+/, '')) : (content || '');
}

const STARTER_BODY = `# When to use

Describe the situations where the agent should apply this skill.

# Instructions

1. Step one.
2. Step two.
`;

interface EditorState {
  skillId: string | null; // null => creating a new skill
  name: string;
  description: string;
  body: string;
  store: 'workspace' | 'volume';
  basePath: string | null;
  writable: boolean;
}

const EMPTY_EDITOR: EditorState = {
  skillId: null,
  name: '',
  description: '',
  body: STARTER_BODY,
  store: 'workspace',
  basePath: null,
  writable: true,
};

export function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [locations, setLocations] = useState<SkillLocation[]>([]);
  const [includeShared, setIncludeShared] = useState(true);
  const [loading, setLoading] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const loadTokenRef = useRef(0);

  const refreshSkills = useCallback(async () => {
    setLoading(true);
    try {
      const [s, locs] = await Promise.all([
        listSkills(includeShared),
        listSkillLocations(includeShared),
      ]);
      setSkills(s);
      setLocations(locs);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, [includeShared]);

  useEffect(() => {
    setError(null);
    refreshSkills();
  }, [refreshSkills]);

  const loadSkill = useCallback(async (skillId: string) => {
    setError(null);
    const token = ++loadTokenRef.current;
    try {
      const full = await getSkill(skillId);
      if (token !== loadTokenRef.current) return;
      setEditor({
        skillId: full.id,
        name: full.name,
        description: full.description,
        body: stripFrontmatter(full.content || ''),
        store: full.store,
        basePath: null,
        writable: full.writable,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load skill');
    }
  }, []);

  const startNew = useCallback(() => {
    setError(null);
    const personal = locations.find((l) => l.is_personal);
    setEditor({
      ...EMPTY_EDITOR,
      store: personal ? personal.store : 'workspace',
      basePath: personal ? personal.base_path : null,
    });
  }, [locations]);

  const handleSave = useCallback(async () => {
    if (!editor) return;
    if (!editor.name.trim()) {
      setError('A skill name is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let saved: Skill;
      if (editor.skillId) {
        saved = await updateSkill(editor.skillId, {
          name: editor.name,
          description: editor.description,
          content: editor.body,
        });
      } else {
        saved = await createSkill({
          name: editor.name,
          description: editor.description,
          content: editor.body,
          store: editor.store,
          base_path: editor.basePath,
        });
      }
      await refreshSkills();
      await loadSkill(saved.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save skill');
    } finally {
      setSaving(false);
    }
  }, [editor, refreshSkills, loadSkill]);

  const handleDelete = useCallback(async () => {
    if (!editor?.skillId) return;
    if (!window.confirm(`Delete skill "${editor.name}"? This cannot be undone.`)) return;
    setSaving(true);
    setError(null);
    try {
      await deleteSkill(editor.skillId);
      setEditor(null);
      await refreshSkills();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete skill');
    } finally {
      setSaving(false);
    }
  }, [editor, refreshSkills]);

  // The embedded agent can author skills too — react to its tool calls so the
  // list stays fresh and the editor reflects whatever it just saved.
  const handleAssistantToolResult = useCallback(
    (toolName: string, result: unknown, ok: boolean) => {
      if (!SKILL_TOOLS.has(toolName)) return;
      refreshSkills();
      if (toolName === 'save_skill' && ok) {
        const skill = (result as { skill?: { id?: string } })?.skill;
        if (skill?.id) loadSkill(skill.id);
      }
    },
    [refreshSkills, loadSkill],
  );

  // Group skills by their storage location for the list rail.
  const grouped = useMemo(() => {
    const groups = new Map<string, Skill[]>();
    for (const s of skills) {
      const key = s.location_label || (s.store === 'workspace' ? 'Personal' : 'Shared');
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(s);
    }
    return Array.from(groups.entries());
  }, [skills]);

  // Distinct create-target locations for the picker.
  const isCreating = editor && !editor.skillId;

  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-2">
          <BookOpen className="w-5 h-5 text-primary" />
          <h2 className="text-base font-semibold text-heading">Skills</h2>
          <span className="text-xs text-gray-500 hidden sm:inline">
            Reusable instructions your agent can load — yours and your team's
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={assistantOpen ? 'default' : 'outline'}
            size="sm"
            onClick={() => setAssistantOpen((v) => !v)}
          >
            <Sparkles className="w-4 h-4 mr-1.5" />
            Assistant
          </Button>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left rail: skills list */}
        <div className="w-72 shrink-0 border-r border-gray-200 flex flex-col min-h-0">
          <div className="p-3 flex items-center gap-2 border-b border-gray-100">
            <Button size="sm" className="flex-1" onClick={startNew}>
              <Plus className="w-4 h-4 mr-1.5" /> New skill
            </Button>
            <button
              onClick={refreshSkills}
              className="p-2 rounded-md hover:bg-gray-100 text-gray-500"
              aria-label="Refresh"
              title="Refresh"
            >
              <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            </button>
          </div>
          <label className="px-3 py-2 flex items-center gap-2 text-xs text-gray-600 border-b border-gray-100 cursor-pointer">
            <input
              type="checkbox"
              checked={includeShared}
              onChange={(e) => setIncludeShared(e.target.checked)}
            />
            Include shared <code className="text-[10px]">.skills</code>
          </label>

          <div className="flex-1 overflow-y-auto p-2 space-y-3">
            {loading && skills.length === 0 && (
              <div className="flex items-center justify-center py-8 text-gray-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            )}
            {!loading && skills.length === 0 && (
              <div className="text-center text-xs text-gray-400 py-8 px-3">
                No skills yet. Create one, or ask the Assistant to draft it for you.
              </div>
            )}
            {grouped.map(([label, items]) => (
              <div key={label}>
                <div className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                  {label === 'Personal' ? (
                    <UserIcon className="w-3 h-3" />
                  ) : (
                    <FolderTree className="w-3 h-3" />
                  )}
                  {label}
                </div>
                {items.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => loadSkill(s.id)}
                    className={cn(
                      'w-full text-left px-2.5 py-2 rounded-md hover:bg-gray-50 transition-colors',
                      editor?.skillId === s.id && 'bg-primary/5 ring-1 ring-primary/30',
                    )}
                  >
                    <div className="text-sm font-medium text-heading truncate">{s.name}</div>
                    {s.description && (
                      <div className="text-xs text-gray-500 line-clamp-2">{s.description}</div>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Middle: editor */}
        <div className="flex-1 flex flex-col min-h-0">
          {!editor ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center text-gray-400 px-8">
              <BookOpen className="w-10 h-10 mb-3 text-gray-300" />
              <p className="text-sm font-medium text-gray-500">Select a skill to view or edit</p>
              <p className="text-xs mt-1 max-w-sm">
                A skill is a <code>SKILL.md</code> the agent loads on demand. Personal skills
                live in your workspace; shared skills live in a <code>.skills</code> folder on a
                Unity Catalog volume your team can read.
              </p>
              <Button className="mt-4" size="sm" onClick={startNew}>
                <Plus className="w-4 h-4 mr-1.5" /> Create a skill
              </Button>
            </div>
          ) : (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="p-4 space-y-3 border-b border-gray-100 shrink-0">
                <div className="flex items-start gap-3">
                  <div className="flex-1 space-y-3">
                    <input
                      value={editor.name}
                      onChange={(e) => setEditor({ ...editor, name: e.target.value })}
                      placeholder="Skill name"
                      disabled={!editor.writable}
                      className="w-full text-lg font-semibold text-heading border-b border-transparent focus:border-gray-300 outline-none bg-transparent disabled:opacity-60"
                    />
                    <input
                      value={editor.description}
                      onChange={(e) => setEditor({ ...editor, description: e.target.value })}
                      placeholder="One-line description — when should the agent use this skill?"
                      disabled={!editor.writable}
                      className="w-full text-sm text-gray-600 border-b border-transparent focus:border-gray-300 outline-none bg-transparent disabled:opacity-60"
                    />
                  </div>
                </div>

                {isCreating && (
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-medium text-gray-500">Save to</label>
                    <select
                      value={`${editor.store}|${editor.basePath ?? ''}`}
                      onChange={(e) => {
                        const [store, base] = e.target.value.split('|');
                        setEditor({
                          ...editor,
                          store: store as 'workspace' | 'volume',
                          basePath: base || null,
                        });
                      }}
                      className="text-sm border border-gray-300 rounded-md px-2 py-1 bg-white"
                    >
                      {locations.map((l) => (
                        <option key={`${l.store}|${l.base_path}`} value={`${l.store}|${l.base_path}`}>
                          {l.is_personal ? `${l.label} (personal)` : l.label}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {!isCreating && (
                  <div className="text-[11px] text-gray-400 font-mono truncate">
                    {editor.store === 'workspace' ? 'Personal' : editor.store} · loaded
                  </div>
                )}
              </div>

              <div className="flex-1 min-h-0">
                <Editor
                  height="100%"
                  defaultLanguage="markdown"
                  language="markdown"
                  value={editor.body}
                  onChange={(v) => setEditor({ ...editor, body: v ?? '' })}
                  theme="vs-light"
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    wordWrap: 'on',
                    scrollBeyondLastLine: false,
                    lineNumbers: 'off',
                    readOnly: !editor.writable,
                  }}
                />
              </div>

              <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-gray-100 shrink-0">
                <div className="text-xs text-red-600 truncate">{error}</div>
                <div className="flex items-center gap-2">
                  {editor.skillId && editor.writable && (
                    <Button variant="outline" size="sm" onClick={handleDelete} disabled={saving}>
                      <Trash2 className="w-4 h-4 mr-1.5" /> Delete
                    </Button>
                  )}
                  <Button size="sm" onClick={handleSave} disabled={saving || !editor.writable}>
                    {saving ? (
                      <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                    ) : (
                      <Save className="w-4 h-4 mr-1.5" />
                    )}
                    {editor.skillId ? 'Save' : 'Create'}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Right: agent assistant */}
        {assistantOpen && (
          <div className="w-[26rem] shrink-0 border-l border-gray-200 flex flex-col min-h-0 bg-gray-50/50">
            <div className="px-4 py-2.5 border-b border-gray-100 flex items-center gap-2 shrink-0">
              <Sparkles className="w-4 h-4 text-primary" />
              <span className="text-sm font-medium text-heading">Skill assistant</span>
            </div>
            <div className="flex-1 min-h-0 p-3">
              <ChatView
                mode="skill_authoring"
                storageKey="chatview_messages_skill_authoring"
                onToolResult={handleAssistantToolResult}
                placeholder="Ask me to draft or improve a skill..."
                welcomeNode={
                  <div className="text-center px-2 pt-2">
                    <Sparkles className="w-6 h-6 text-primary mx-auto mb-2" />
                    <p className="text-xs text-gray-500">
                      I can draft a new skill, refine an existing one, or explain how skills work.
                    </p>
                  </div>
                }
                samplePrompts={[
                  'Draft a skill for requesting table access the right way.',
                  'Show me my skills.',
                  'Improve the instructions in my onboarding skill.',
                  'Create a shared skill for our team’s data-tagging standards.',
                ]}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
