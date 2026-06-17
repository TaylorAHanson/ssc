import { useState, useEffect, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Loader2, Plus, Wrench, Trash2, RefreshCw, Server, Cpu, Search, X, Database } from 'lucide-react';
import { InfoTip } from '../../components/ui/InfoTip';
import {
  getToolRegistry,
  updateRegistryTool,
  syncLocalTools,
  quickAddMcpSource,
  getAvailableMcpSources,
  deleteMcpSource,
  syncMcpSource,
  type RegistryTool,
  type McpSource,
  type RegistryToolUpdate,
  type AvailableMcpSource,
} from '../../services/api';
import { format, parseISO } from 'date-fns';

// Internal roles a tool can be restricted to. Empty selection = all roles.
const KNOWN_ROLES = ['Platform Admin', 'Governance Admin', 'Security Admin', 'Finance Admin', 'User'];

export function ToolRegistry() {
  const [tools, setTools] = useState<RegistryTool[]>([]);
  const [sources, setSources] = useState<McpSource[]>([]);
  const [sourceKinds, setSourceKinds] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // New-source form
  const [showSourceForm, setShowSourceForm] = useState(false);
  const [srcName, setSrcName] = useState('');
  const [srcUrl, setSrcUrl] = useState('');
  const [srcKind, setSrcKind] = useState('managed_functions');
  const [srcIdentity, setSrcIdentity] = useState<'sp' | 'obo'>('obo');
  const [srcAutoEnable, setSrcAutoEnable] = useState(true);
  const [addingSource, setAddingSource] = useState(false);

  // "Browse from Databricks" picker
  const [showBrowse, setShowBrowse] = useState(false);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [available, setAvailable] = useState<AvailableMcpSource[]>([]);
  const [browseQuery, setBrowseQuery] = useState('');

  // Tool table filters
  const [search, setSearch] = useState('');
  const [surfaceFilter, setSurfaceFilter] = useState<'all' | 'main' | 'workflow_agent' | 'workflow_exec' | 'unassigned'>('all');
  const [originFilter, setOriginFilter] = useState<'all' | 'local' | 'workflow' | 'mcp'>('all');

  const sourceNameById = useMemo(() => {
    const map: Record<string, string> = {};
    sources.forEach((s) => (map[s.id] = s.name));
    return map;
  }, [sources]);

  const filteredAvailable = useMemo(() => {
    const q = browseQuery.trim().toLowerCase();
    if (!q) return available;
    return available.filter((c) =>
      [c.name, c.kind, c.detail, c.server_url]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(q))
    );
  }, [available, browseQuery]);

  const filteredTools = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tools.filter((t) => {
      if (originFilter !== 'all' && t.origin !== originFilter) return false;
      if (surfaceFilter === 'main' && !t.enabled_for_main_agent) return false;
      if (surfaceFilter === 'workflow_agent' && !t.enabled_for_workflow_agent) return false;
      if (surfaceFilter === 'workflow_exec' && !t.enabled_for_workflow_execution) return false;
      if (
        surfaceFilter === 'unassigned' &&
        (t.enabled_for_main_agent || t.enabled_for_workflow_agent || t.enabled_for_workflow_execution)
      )
        return false;
      if (q) {
        const haystack = `${t.tool_name} ${t.description || ''} ${t.source_id ? sourceNameById[t.source_id] || '' : ''}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [tools, search, surfaceFilter, originFilter, sourceNameById]);

  const flash = (type: 'success' | 'error', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 3500);
  };

  const load = async () => {
    setIsLoading(true);
    try {
      const data = await getToolRegistry();
      setTools(data.tools);
      setSources(data.sources);
      setSourceKinds(data.source_kinds);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to load tool registry');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const patchTool = async (tool: RegistryTool, patch: RegistryToolUpdate) => {
    setBusyId(tool.id);
    // Optimistic update so toggles feel instant.
    setTools((prev) => prev.map((t) => (t.id === tool.id ? { ...t, ...patch } as RegistryTool : t)));
    try {
      const updated = await updateRegistryTool(tool.id, patch);
      setTools((prev) => prev.map((t) => (t.id === tool.id ? updated : t)));
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to update tool');
      await load(); // revert to server truth
    } finally {
      setBusyId(null);
    }
  };

  const toggleRole = (tool: RegistryTool, role: string) => {
    const current = new Set(tool.allowed_roles || []);
    if (current.has(role)) current.delete(role);
    else current.add(role);
    patchTool(tool, { allowed_roles: Array.from(current) });
  };

  // Edit the per-tool success check. The predicate is a JSON $-expression
  // (see backend app/workflows/expr.py); a tiny prompt keeps this lightweight
  // for the relatively rare case of an external tool that 200s on failure.
  const editSuccessPredicate = (tool: RegistryTool) => {
    const current = tool.success_predicate
      ? JSON.stringify(tool.success_predicate, null, 2)
      : '';
    const example = '{"$eq": [{"$var": "result.state"}, "submitted"]}';
    const input = window.prompt(
      `Success check for "${tool.tool_name}".\n\n` +
        'Enter a JSON $-expression evaluated against {result}. The tool is treated ' +
        'as failed if it evaluates falsy (catches HTTP-200 results that actually failed). ' +
        'Leave blank to clear.\n\n' +
        `Example: ${example}`,
      current
    );
    if (input === null) return; // cancelled
    const trimmed = input.trim();
    if (trimmed === '') {
      patchTool(tool, { success_predicate: null });
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      flash('error', 'Success check must be valid JSON.');
      return;
    }
    patchTool(tool, { success_predicate: parsed });
  };

  const handleSyncLocal = async () => {
    try {
      const res = await syncLocalTools();
      flash('success', `Synced local tools (${res.inserted} new).`);
      await load();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to sync local tools');
    }
  };

  const resetSourceForm = () => {
    setSrcName('');
    setSrcUrl('');
    setSrcKind('managed_functions');
    setSrcIdentity('obo');
    setSrcAutoEnable(true);
  };

  // Pull MCP servers from Databricks (AI Gateway connections, Genie spaces, MCP
  // apps) so the admin can pick instead of hand-typing a name + URL.
  const handleBrowse = async () => {
    setShowBrowse(true);
    setBrowseLoading(true);
    try {
      const res = await getAvailableMcpSources();
      setAvailable(res.sources);
      if (res.sources.length === 0) {
        flash('error', 'No MCP servers found in the workspace (the Service Principal may lack access).');
      }
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to list workspace MCP servers');
      setShowBrowse(false);
    } finally {
      setBrowseLoading(false);
    }
  };

  // Prefill the Add form from a picked workspace candidate so the admin can
  // review identity / auto-enable before connecting.
  const pickCandidate = (c: AvailableMcpSource) => {
    setSrcName(c.name);
    setSrcUrl(c.server_url);
    setSrcKind(c.kind);
    setShowBrowse(false);
    setShowSourceForm(true);
  };

  // One-shot: register the server, discover its tools, and (optionally) enable
  // the read-only ones for the main agent — so it's usable immediately instead
  // of register → sync → toggle-each-tool.
  const handleCreateSource = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddingSource(true);
    try {
      const res = await quickAddMcpSource({
        name: srcName,
        server_url: srcUrl,
        kind: srcKind,
        default_identity_mode: srcIdentity,
        auto_enable_read_only: srcAutoEnable,
      });
      if (res.discovery.ok) {
        const enabledNote = srcAutoEnable
          ? ` ${res.auto_enabled} read-only tool(s) enabled for the main agent.`
          : '';
        flash('success', `Connected "${res.source.name}": discovered ${res.discovery.count} tool(s).${enabledNote}`);
        resetSourceForm();
        setShowSourceForm(false);
      } else {
        // The source was created but discovery failed — keep the form open with
        // the error so the admin can fix the URL/scope and retry.
        flash('error', `Added "${res.source.name}" but discovery failed: ${res.discovery.error}`);
      }
      await load();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to add source');
    } finally {
      setAddingSource(false);
    }
  };

  const handleSyncSource = async (source: McpSource) => {
    setBusyId(source.id);
    try {
      const res = await syncMcpSource(source.id);
      if (res.ok) flash('success', `Discovered ${res.count} tool(s) from ${source.name}.`);
      else flash('error', `Sync failed: ${res.error}`);
      await load();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to sync source');
    } finally {
      setBusyId(null);
    }
  };

  const handleDeleteSource = async (source: McpSource) => {
    if (!confirm(`Delete source "${source.name}" and all of its discovered tools?`)) return;
    try {
      await deleteMcpSource(source.id);
      flash('success', 'Source deleted.');
      await load();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'Failed to delete source');
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Wrench className="w-5 h-5" /> Tool Registry
          </h2>
          <p className="text-gray-500 text-sm mt-1">
            One catalog for every tool. Toggle availability per usage context (Main Agent chat,
            Workflow Agent authoring, or Used in Workflows as a building block), restrict by role,
            and choose Service Principal or On-Behalf-Of identity.
          </p>
        </div>
        <Button variant="outline" onClick={handleSyncLocal} className="flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Sync Local Tools
        </Button>
      </div>

      {message && (
        <div className={`p-3 rounded-md text-sm ${message.type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {message.text}
        </div>
      )}

      {/* ─── MCP Sources ─────────────────────────────────────────── */}
      <Card>
        <CardHeader className="bg-gray-50/50 border-b flex flex-row items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="w-4 h-4" /> MCP Sources (Databricks Unity AI Gateway)
          </CardTitle>
          {!showSourceForm && (
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={handleBrowse} className="flex items-center gap-2">
                <Database className="w-4 h-4" /> Browse from Databricks
              </Button>
              <Button size="sm" onClick={() => setShowSourceForm(true)} className="flex items-center gap-2">
                <Plus className="w-4 h-4" /> Add Source
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="pt-4 space-y-4">
          {showBrowse && (
            <div className="rounded-md border border-gray-200 bg-gray-50/50">
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200">
                <span className="text-sm font-medium text-gray-700 flex items-center gap-2">
                  <Database className="w-4 h-4" /> MCP servers in your workspace
                </span>
                <button onClick={() => setShowBrowse(false)} className="text-gray-400 hover:text-gray-700" title="Close">
                  <X className="w-4 h-4" />
                </button>
              </div>
              {browseLoading ? (
                <div className="py-8 flex justify-center">
                  <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
              ) : available.length === 0 ? (
                <p className="text-sm text-gray-500 px-3 py-4">No MCP servers found in the workspace.</p>
              ) : (
                <>
                  <div className="relative px-3 py-2 border-b border-gray-100">
                    <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <Input
                      value={browseQuery}
                      onChange={(e) => setBrowseQuery(e.target.value)}
                      placeholder="Quick search MCP servers in your workspace…"
                      className="pl-9 h-9"
                    />
                  </div>
                  {filteredAvailable.length === 0 ? (
                    <p className="text-sm text-gray-500 px-3 py-4">No servers match “{browseQuery}”.</p>
                  ) : (
                <ul className="divide-y divide-gray-100 max-h-72 overflow-auto">
                  {filteredAvailable.map((c) => (
                    <li key={c.server_url} className="flex items-center justify-between px-3 py-2 gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-900 truncate">{c.name}</span>
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-indigo-100 text-indigo-800">{c.kind}</span>
                        </div>
                        <div className="text-xs text-gray-500 truncate" title={c.server_url}>{c.detail || c.server_url}</div>
                      </div>
                      {c.already_registered ? (
                        <span className="text-xs text-gray-400 whitespace-nowrap">Already added</span>
                      ) : (
                        <Button size="sm" onClick={() => pickCandidate(c)} className="flex items-center gap-2 whitespace-nowrap">
                          <Plus className="w-3.5 h-3.5" /> Use
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
                  )}
                </>
              )}
            </div>
          )}
          {showSourceForm && (
            <form onSubmit={handleCreateSource} className="grid grid-cols-2 gap-3 p-3 rounded-md border border-gray-200 bg-gray-50/50">
              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-600">Name</label>
                <Input value={srcName} onChange={(e) => setSrcName(e.target.value)} placeholder="UC system.ai functions" required disabled={addingSource} />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-600">Server URL</label>
                <Input value={srcUrl} onChange={(e) => setSrcUrl(e.target.value)} placeholder="https://<host>/api/2.0/mcp/functions/system/ai" required disabled={addingSource} />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-600">Kind</label>
                <select value={srcKind} onChange={(e) => setSrcKind(e.target.value)} disabled={addingSource} className="w-full h-10 px-3 border border-gray-300 rounded-md bg-white text-sm disabled:opacity-50">
                  {(sourceKinds.length ? sourceKinds : ['managed_functions']).map((k) => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-600">Default Identity</label>
                <select value={srcIdentity} onChange={(e) => setSrcIdentity(e.target.value as 'sp' | 'obo')} disabled={addingSource} className="w-full h-10 px-3 border border-gray-300 rounded-md bg-white text-sm disabled:opacity-50">
                  <option value="obo">On-Behalf-Of user (OBO)</option>
                  <option value="sp">Service Principal (SP)</option>
                </select>
              </div>
              <label className="col-span-2 flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={srcAutoEnable}
                  onChange={(e) => setSrcAutoEnable(e.target.checked)}
                  disabled={addingSource}
                  className="w-4 h-4"
                />
                Enable read-only tools for the Main Agent immediately (mutating tools stay off until you opt in)
              </label>
              <div className="col-span-2 flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => { setShowSourceForm(false); resetSourceForm(); }} disabled={addingSource}>Cancel</Button>
                <Button type="submit" disabled={addingSource} className="flex items-center gap-2">
                  {addingSource ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                  {addingSource ? 'Connecting…' : 'Add & Connect'}
                </Button>
              </div>
            </form>
          )}

          {sources.length === 0 ? (
            <p className="text-sm text-gray-500 py-2">
              No MCP sources yet. Add one (e.g. a managed UC functions, Genie, AI Search, or external
              connection endpoint) — it's registered, its tools are discovered, and read-only tools go
              live for the Main Agent in one step.
            </p>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="text-gray-500 text-xs uppercase">
                <tr>
                  <th className="py-2">Source</th>
                  <th className="py-2">URL</th>
                  <th className="py-2">Identity</th>
                  <th className="py-2">Last Sync</th>
                  <th className="py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sources.map((s) => (
                  <tr key={s.id}>
                    <td className="py-2">
                      <div className="font-medium text-gray-900">{s.name}</div>
                      <div className="text-xs text-gray-500">{s.kind}</div>
                    </td>
                    <td className="py-2 text-gray-600 max-w-[280px] truncate" title={s.server_url}>{s.server_url}</td>
                    <td className="py-2 uppercase text-xs text-gray-600">{s.default_identity_mode}</td>
                    <td className="py-2">
                      {s.last_sync_status === 'ok' ? (
                        <span className="text-xs text-green-700">
                          {s.last_tool_count ?? 0} tools
                          {s.last_synced_at ? ` · ${format(parseISO(s.last_synced_at), 'MMM d HH:mm')}` : ''}
                        </span>
                      ) : s.last_sync_status === 'error' ? (
                        <span className="text-xs text-red-600" title={s.last_sync_error || ''}>Error</span>
                      ) : (
                        <span className="text-xs text-gray-400">Never</span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => handleSyncSource(s)}
                          disabled={busyId === s.id}
                          className="p-1 text-gray-400 hover:text-blue-600 transition-colors disabled:opacity-50"
                          title="Sync tools"
                        >
                          {busyId === s.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                        </button>
                        <button onClick={() => handleDeleteSource(s)} className="p-1 text-gray-400 hover:text-red-600 transition-colors" title="Delete source">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {/* ─── Tools ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="bg-gray-50/50 border-b space-y-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Cpu className="w-4 h-4" /> Tools ({filteredTools.length}
            {filteredTools.length !== tools.length ? ` of ${tools.length}` : ''})
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[220px]">
              <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search tools by name, description, or source…"
                className="pl-9 pr-9"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
                  title="Clear search"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
            <select
              value={surfaceFilter}
              onChange={(e) => setSurfaceFilter(e.target.value as typeof surfaceFilter)}
              className="h-10 px-3 border border-gray-300 rounded-md bg-white text-sm"
            >
              <option value="all">All contexts</option>
              <option value="main">Main Agent enabled</option>
              <option value="workflow_agent">Workflow Agent enabled</option>
              <option value="workflow_exec">Used in Workflows</option>
              <option value="unassigned">Unassigned</option>
            </select>
            <select
              value={originFilter}
              onChange={(e) => setOriginFilter(e.target.value as typeof originFilter)}
              className="h-10 px-3 border border-gray-300 rounded-md bg-white text-sm"
            >
              <option value="all">All origins</option>
              <option value="local">Local</option>
              <option value="workflow">Provider</option>
              <option value="mcp">MCP</option>
            </select>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="py-12 flex justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : tools.length === 0 ? (
            <div className="py-12 text-center text-gray-500">No tools registered.</div>
          ) : filteredTools.length === 0 ? (
            <div className="py-12 text-center text-gray-500">No tools match your filters.</div>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-900 font-medium">
                <tr>
                  <th className="p-3">
                    <span className="inline-flex items-center gap-1">Tool
                      <InfoTip align="left" text="The tool's name and description as exposed to the agents." />
                    </span>
                  </th>
                  <th className="p-3">
                    <span className="inline-flex items-center gap-1">Source
                      <InfoTip align="left" text="Where the tool comes from: 'Local' (chat tool defined in this app), 'Provider' (a workflow building block backed by a provider integration), or an MCP source (discovered from a Databricks Unity AI Gateway server)." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Main Agent
                      <InfoTip text="Available to the main unified chat agent (EDH) when checked." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Workflow Agent
                      <InfoTip text="Available to the workflow-authoring chat assistant when checked." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Used in Workflows
                      <InfoTip text="Usable as a workflow building block (a graph step tool) when checked. This is how provider/mutating tools are exposed to workflow execution." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">MCP
                      <InfoTip text="Publish this tool over the in-app MCP server (/mcp) so external agents/apps (e.g. via Databricks AI Gateway) can call it. Takes effect after the server restarts." />
                    </span>
                  </th>
                  <th className="p-3">
                    <span className="inline-flex items-center gap-1">Roles
                      <InfoTip text="Restrict the tool to users with at least one of the selected roles. No roles selected = available to everyone." />
                    </span>
                  </th>
                  <th className="p-3">
                    <span className="inline-flex items-center gap-1">Identity
                      <InfoTip text="Execution identity: OBO runs as the calling user (On-Behalf-Of); SP runs as the app's Service Principal." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Mutating
                      <InfoTip text="Marks the tool as having side effects (writes/changes state). Mutating tools are subject to policy enforcement and idempotency handling." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Success check
                      <InfoTip align="right" text="Optional success check: a JSON $-expression evaluated against the tool's result. Catches tools that return HTTP 200 but actually failed (e.g. an external/MCP call). When the check evaluates falsy the call is treated as a failure and a workflow will not advance." />
                    </span>
                  </th>
                  <th className="p-3 text-center">
                    <span className="inline-flex items-center gap-1">Enabled
                      <InfoTip align="right" text="Master switch. When off, the tool is unavailable to every agent surface regardless of the Main Agent/Workflow settings." />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredTools.map((t) => (
                  <tr key={t.id} className={`hover:bg-gray-50 ${busyId === t.id ? 'opacity-60' : ''}`}>
                    <td className="p-3">
                      <div className="font-medium text-gray-900">{t.tool_name}</div>
                      {t.description && (
                        <div className="text-xs text-gray-500 max-w-[260px] truncate" title={t.description}>{t.description}</div>
                      )}
                    </td>
                    <td className="p-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium cursor-help ${t.origin === 'local' ? 'bg-gray-100 text-gray-700' : t.origin === 'workflow' ? 'bg-amber-100 text-amber-800' : 'bg-indigo-100 text-indigo-800'}`}
                        title={t.origin === 'local'
                          ? 'Local: a chat tool defined in this app.'
                          : t.origin === 'workflow'
                            ? 'Provider: a workflow building block backed by a provider integration.'
                            : `MCP: discovered from the Unity AI Gateway source${t.source_id && sourceNameById[t.source_id] ? ` "${sourceNameById[t.source_id]}"` : ''}.`}
                      >
                        {t.origin === 'local'
                          ? 'Local'
                          : t.origin === 'workflow'
                            ? 'Provider'
                            : (t.source_id ? sourceNameById[t.source_id] || 'MCP' : 'MCP')}
                      </span>
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.enabled_for_main_agent} onChange={(e) => patchTool(t, { enabled_for_main_agent: e.target.checked })} className="w-4 h-4 cursor-pointer" title="Available to the main unified chat agent (EDH) when checked." />
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.enabled_for_workflow_agent} onChange={(e) => patchTool(t, { enabled_for_workflow_agent: e.target.checked })} className="w-4 h-4 cursor-pointer" title="Available to the workflow-authoring chat assistant when checked." />
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.enabled_for_workflow_execution} onChange={(e) => patchTool(t, { enabled_for_workflow_execution: e.target.checked })} className="w-4 h-4 cursor-pointer" title="Usable as a workflow building block (a graph step tool) when checked." />
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.exposed_via_mcp} onChange={(e) => patchTool(t, { exposed_via_mcp: e.target.checked })} className="w-4 h-4 cursor-pointer" title="Publish this tool over the in-app MCP server (/mcp) so external agents/apps (e.g. via Databricks AI Gateway) can call it. Takes effect after the server restarts." />
                    </td>
                    <td className="p-3">
                      <div className="flex flex-wrap gap-1 max-w-[220px]">
                        {KNOWN_ROLES.map((role) => {
                          const active = (t.allowed_roles || []).includes(role);
                          return (
                            <button
                              key={role}
                              onClick={() => toggleRole(t, role)}
                              className={`px-1.5 py-0.5 rounded text-[10px] font-medium border transition-colors ${active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-400'}`}
                              title={active ? `Restricted to ${role}` : `Allow ${role}`}
                            >
                              {role.replace(' Admin', '')}
                            </button>
                          );
                        })}
                      </div>
                      {(!t.allowed_roles || t.allowed_roles.length === 0) && (
                        <div className="text-[10px] text-gray-400 mt-0.5">All roles</div>
                      )}
                    </td>
                    <td className="p-3">
                      <select
                        value={t.identity_mode}
                        onChange={(e) => patchTool(t, { identity_mode: e.target.value as 'sp' | 'obo' })}
                        className="h-8 px-2 border border-gray-300 rounded-md bg-white text-xs cursor-pointer"
                        title="Execution identity: OBO runs as the calling user (On-Behalf-Of); SP runs as the app's Service Principal."
                      >
                        <option value="obo">OBO</option>
                        <option value="sp">SP</option>
                      </select>
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.is_mutating} onChange={(e) => patchTool(t, { is_mutating: e.target.checked })} className="w-4 h-4" title={`side effect: ${t.side_effect_class}`} />
                    </td>
                    <td className="p-3 text-center">
                      <button
                        type="button"
                        onClick={() => editSuccessPredicate(t)}
                        className={`px-2 py-0.5 rounded text-[10px] font-medium border transition-colors ${t.success_predicate ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-400'}`}
                        title={t.success_predicate ? `Custom success check:\n${JSON.stringify(t.success_predicate)}` : 'No custom success check (default heuristics). Click to add.'}
                      >
                        {t.success_predicate ? 'Custom' : 'Default'}
                      </button>
                    </td>
                    <td className="p-3 text-center">
                      <input type="checkbox" checked={t.enabled} onChange={(e) => patchTool(t, { enabled: e.target.checked })} className="w-4 h-4 cursor-pointer" title="Master switch. When off, the tool is unavailable to every agent surface regardless of the Main Agent/Workflow settings." />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
