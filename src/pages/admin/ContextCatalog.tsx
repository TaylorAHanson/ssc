import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  Library,
  Plus,
  Trash2,
  Loader2,
  Save,
  Upload,
  Download,
  FileText,
  FolderTree,
  Search,
  X,
  Pencil,
  ChevronRight,
  CheckCircle2,
  Circle,
  Clock,
  AlertTriangle,
  Boxes,
  Tag,
  User,
  TrendingUp,
} from 'lucide-react';
import { api } from '../../services/api';
import type {
  ContextDomain,
  ContextDomainDetail,
  ContextDocument,
  ContextDocumentSummary,
  ContextSearchResult,
} from '../../services/api';
import { ImportContextCatalogModal } from '../../components/admin/ImportContextCatalogModal';

const inputClass =
  'w-full border border-gray-300 rounded-md h-10 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent';
const textareaClass =
  'w-full border border-gray-300 rounded-md p-3 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-accent';

interface DomainFormState {
  name: string;
  description: string;
  parent_id: string;
  domain_type: string;
  primary_owner: string;
  secondary_owner: string;
  reviewers: string;
  categories: string;
}

const emptyDomainForm: DomainFormState = {
  name: '',
  description: '',
  parent_id: '',
  domain_type: 'community',
  primary_owner: '',
  secondary_owner: '',
  reviewers: '',
  categories: '',
};

interface DocFormState {
  id: string | null;
  title: string;
  status: string;
  body_markdown: string;
}

const emptyDocForm: DocFormState = {
  id: null,
  title: '',
  status: 'published',
  body_markdown: '',
};

const splitList = (value: string): string[] =>
  value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);

// Documents older than this read as "stale" — a nudge to review/refresh them.
const STALE_DAYS = 90;
const DAY_MS = 86_400_000;

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < DAY_MS) return `${Math.floor(diff / 3_600_000)}h ago`;
  const days = Math.floor(diff / DAY_MS);
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

function isStale(iso: string | null): boolean {
  if (!iso) return false;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return false;
  return Date.now() - then > STALE_DAYS * DAY_MS;
}

interface QualityCheck {
  label: string;
  ok: boolean;
}

// Lightweight, heuristic completeness signal for a domain. No backend infra —
// just structural checks an admin can act on to improve agent answer quality.
function domainChecks(detail: ContextDomainDetail): QualityCheck[] {
  const docs = detail.documents || [];
  const published = docs.filter((d) => d.status === 'published').length;
  return [
    { label: 'Description', ok: !!detail.description?.trim() },
    { label: 'Primary owner', ok: !!detail.primary_owner },
    { label: 'Reviewers', ok: (detail.reviewers || []).length > 0 },
    { label: 'Categories', ok: (detail.categories || []).length > 0 },
    { label: 'Published doc', ok: published > 0 },
  ];
}

function scoreColor(ratio: number): string {
  if (ratio >= 0.999) return 'text-green-600';
  if (ratio >= 0.6) return 'text-amber-600';
  return 'text-red-600';
}

function barColor(ratio: number): string {
  if (ratio >= 0.999) return 'bg-green-500';
  if (ratio >= 0.6) return 'bg-amber-500';
  return 'bg-red-500';
}

export function ContextCatalog() {
  const [domains, setDomains] = useState<ContextDomain[]>([]);
  const [isLoadingDomains, setIsLoadingDomains] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ContextDomainDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  const [showDomainForm, setShowDomainForm] = useState(false);
  const [domainForm, setDomainForm] = useState<DomainFormState>(emptyDomainForm);
  const [editingDomain, setEditingDomain] = useState(false);
  const [isSavingDomain, setIsSavingDomain] = useState(false);

  const [docForm, setDocForm] = useState<DocFormState | null>(null);
  const [isSavingDoc, setIsSavingDoc] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ContextSearchResult[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showImport, setShowImport] = useState(false);

  // ---------------------------------------------------------------- load data

  const loadDomains = async (selectAfter?: string | null) => {
    setIsLoadingDomains(true);
    try {
      const list = await api.listContextDomains();
      setDomains(list);
      if (selectAfter !== undefined) {
        setSelectedId(selectAfter);
      } else if (selectAfter === undefined && !selectedId && list.length > 0) {
        setSelectedId(list[0].id);
      }
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to load domains' });
    } finally {
      setIsLoadingDomains(false);
    }
  };

  useEffect(() => {
    loadDomains();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let mounted = true;
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setIsLoadingDetail(true);
    (async () => {
      try {
        const d = await api.getContextDomain(selectedId);
        if (mounted) setDetail(d);
      } catch (e) {
        if (mounted)
          setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to load domain' });
      } finally {
        if (mounted) setIsLoadingDetail(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [selectedId]);

  // Build a flat, depth-annotated tree for rendering / parent selection.
  const tree = useMemo(() => {
    const byParent: Record<string, ContextDomain[]> = {};
    for (const d of domains) {
      const key = d.parent_id || '__root__';
      (byParent[key] = byParent[key] || []).push(d);
    }
    const rows: { domain: ContextDomain; depth: number }[] = [];
    const walk = (parentKey: string, depth: number) => {
      const children = (byParent[parentKey] || []).sort((a, b) => a.name.localeCompare(b.name));
      for (const child of children) {
        rows.push({ domain: child, depth });
        walk(child.id, depth + 1);
      }
    };
    walk('__root__', 0);
    return rows;
  }, [domains]);

  // Catalog-wide rollups for the overview strip. Computed from the cheap list
  // payload, so no extra requests.
  const stats = useMemo(() => {
    const totalDocs = domains.reduce((sum, d) => sum + (d.document_count ?? 0), 0);
    const withOwner = domains.filter((d) => !!d.primary_owner).length;
    const categories = new Set<string>();
    for (const d of domains) for (const c of d.categories || []) categories.add(c);
    const ownerCoverage = domains.length ? Math.round((withOwner / domains.length) * 100) : 0;
    return {
      domainCount: domains.length,
      totalDocs,
      ownerCoverage,
      categoryCount: categories.size,
    };
  }, [domains]);

  // ---------------------------------------------------------------- domain ops

  const openCreateDomain = () => {
    setDocForm(null);
    setEditingDomain(false);
    setDomainForm({ ...emptyDomainForm, parent_id: selectedId || '' });
    setShowDomainForm(true);
  };

  const openEditDomain = () => {
    if (!detail) return;
    setDocForm(null);
    setEditingDomain(true);
    setDomainForm({
      name: detail.name,
      description: detail.description || '',
      parent_id: detail.parent_id || '',
      domain_type: detail.domain_type,
      primary_owner: detail.primary_owner || '',
      secondary_owner: detail.secondary_owner || '',
      reviewers: (detail.reviewers || []).join(', '),
      categories: (detail.categories || []).join(', '),
    });
    setShowDomainForm(true);
  };

  const saveDomain = async () => {
    if (!domainForm.name.trim()) {
      setMessage({ type: 'error', text: 'Domain name is required' });
      return;
    }
    setIsSavingDomain(true);
    setMessage(null);
    try {
      const payload = {
        name: domainForm.name.trim(),
        description: domainForm.description.trim() || undefined,
        parent_id: domainForm.parent_id || null,
        domain_type: domainForm.domain_type,
        primary_owner: domainForm.primary_owner.trim() || undefined,
        secondary_owner: domainForm.secondary_owner.trim() || undefined,
        reviewers: splitList(domainForm.reviewers),
        categories: splitList(domainForm.categories),
      };
      if (editingDomain && detail) {
        const updated = await api.updateContextDomain(detail.id, payload);
        setMessage({ type: 'success', text: `Updated domain "${updated.name}"` });
        await loadDomains(detail.id);
        const d = await api.getContextDomain(detail.id);
        setDetail(d);
      } else {
        const created = await api.createContextDomain(payload);
        setMessage({ type: 'success', text: `Created domain "${created.name}"` });
        await loadDomains(created.id);
      }
      setShowDomainForm(false);
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to save domain' });
    } finally {
      setIsSavingDomain(false);
    }
  };

  const deleteDomain = async () => {
    if (!detail) return;
    if (
      !window.confirm(
        `Delete domain "${detail.name}"? This also deletes its sub-domains and all their documents. This cannot be undone.`
      )
    )
      return;
    try {
      await api.deleteContextDomain(detail.id);
      setMessage({ type: 'success', text: `Deleted domain "${detail.name}"` });
      setSelectedId(null);
      setDetail(null);
      await loadDomains(null);
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to delete domain' });
    }
  };

  // -------------------------------------------------------------- document ops

  const refreshDetail = async () => {
    if (!selectedId) return;
    const d = await api.getContextDomain(selectedId);
    setDetail(d);
    await loadDomains(selectedId);
  };

  const openNewDoc = () => {
    setShowDomainForm(false);
    setDocForm({ ...emptyDocForm });
  };

  const openEditDoc = async (summary: ContextDocumentSummary) => {
    setShowDomainForm(false);
    try {
      const full: ContextDocument = await api.getContextDocument(summary.id);
      setDocForm({
        id: full.id,
        title: full.title,
        status: full.status,
        body_markdown: full.body_markdown || '',
      });
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to open document' });
    }
  };

  const saveDoc = async () => {
    if (!docForm || !detail) return;
    if (!docForm.title.trim()) {
      setMessage({ type: 'error', text: 'Document title is required' });
      return;
    }
    setIsSavingDoc(true);
    setMessage(null);
    try {
      if (docForm.id) {
        await api.updateContextDocument(docForm.id, {
          title: docForm.title.trim(),
          status: docForm.status,
          body_markdown: docForm.body_markdown,
        });
        setMessage({ type: 'success', text: 'Document saved' });
      } else {
        await api.createContextDocument(detail.id, {
          title: docForm.title.trim(),
          status: docForm.status,
          body_markdown: docForm.body_markdown,
          doc_type: 'markdown',
        });
        setMessage({ type: 'success', text: 'Document created' });
      }
      setDocForm(null);
      await refreshDetail();
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to save document' });
    } finally {
      setIsSavingDoc(false);
    }
  };

  const deleteDoc = async (summary: ContextDocumentSummary) => {
    if (!window.confirm(`Delete document "${summary.title}"?`)) return;
    try {
      await api.deleteContextDocument(summary.id);
      setMessage({ type: 'success', text: 'Document deleted' });
      await refreshDetail();
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to delete document' });
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !detail) return;
    setIsUploading(true);
    setMessage(null);
    try {
      await api.uploadContextDocument(detail.id, file);
      setMessage({ type: 'success', text: `Uploaded "${file.name}"` });
      await refreshDetail();
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof Error ? err.message : 'Upload failed' });
    } finally {
      setIsUploading(false);
    }
  };

  // ------------------------------------------------------------- export/import

  const exportBundle = async (opts: { domainIds?: string[]; publishedOnly?: boolean } = {}) => {
    setMessage(null);
    try {
      const bundle = await api.exportContextCatalogBundle(opts);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const scope = opts.domainIds?.length ? 'domain' : 'catalog';
      a.download = `context-${scope}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to export catalog' });
    }
  };

  const handleImported = async () => {
    await loadDomains(selectedId ?? undefined);
    if (selectedId) {
      try {
        setDetail(await api.getContextDomain(selectedId));
      } catch {
        /* selection may have changed shape; list reload above is enough */
      }
    }
  };

  // ------------------------------------------------------------------- search

  const runSearch = async () => {
    if (searchQuery.trim().length < 2) return;
    setIsSearching(true);
    try {
      const res = await api.searchContextCatalog(searchQuery.trim());
      setSearchResults(res.results);
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Search failed' });
    } finally {
      setIsSearching(false);
    }
  };

  // -------------------------------------------------------------------- render

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-heading">
                <Library className="w-6 h-6 text-accent" /> Context Catalog
              </CardTitle>
              <CardDescription>
                Curate the knowledge the agent retrieves from. Organize context into domains and author
                markdown or upload documents (docx, pptx, pdf). Published documents become searchable by
                the agent.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => exportBundle()}
                disabled={domains.length === 0}
                title="Export the whole catalog as a portable bundle"
              >
                <Download className="w-4 h-4 mr-1" /> Export
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowImport(true)}
                title="Import a bundle from another environment"
              >
                <Upload className="w-4 h-4 mr-1" /> Import
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                className={`${inputClass} pl-9`}
                placeholder="Test retrieval — search the catalog the way the agent would..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              />
            </div>
            <Button onClick={runSearch} disabled={isSearching || searchQuery.trim().length < 2}>
              {isSearching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
            </Button>
            {searchResults !== null && (
              <Button variant="outline" onClick={() => setSearchResults(null)}>
                Clear
              </Button>
            )}
          </div>

          {searchResults !== null && (
            <div className="mt-4 space-y-2">
              {searchResults.length === 0 ? (
                <p className="text-sm text-gray-500">No matching passages found.</p>
              ) : (
                searchResults.map((r, i) => (
                  <div key={`${r.document_id}-${r.chunk_index}-${i}`} className="border border-gray-200 rounded-md p-3 bg-gray-50">
                    <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                      <span className="font-semibold text-gray-700">{r.document_title}</span>
                      <span>
                        {r.domain_name} · score {r.score}
                      </span>
                    </div>
                    <p className="text-sm text-gray-700 whitespace-pre-wrap line-clamp-4">{r.content}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={<FolderTree className="w-4 h-4" />} label="Domains" value={stats.domainCount} />
        <StatCard icon={<FileText className="w-4 h-4" />} label="Documents" value={stats.totalDocs} />
        <StatCard
          icon={<User className="w-4 h-4" />}
          label="Owner coverage"
          value={`${stats.ownerCoverage}%`}
          tone={stats.ownerCoverage >= 80 ? 'good' : stats.ownerCoverage >= 50 ? 'warn' : 'bad'}
        />
        <StatCard icon={<Tag className="w-4 h-4" />} label="Categories" value={stats.categoryCount} />
      </div>

      {message && (
        <div
          className={`rounded-md border p-3 text-sm flex items-center justify-between ${
            message.type === 'success'
              ? 'bg-green-50 border-green-200 text-green-800'
              : 'bg-red-50 border-red-200 text-red-800'
          }`}
        >
          <span>{message.text}</span>
          <button onClick={() => setMessage(null)} className="text-current opacity-60 hover:opacity-100">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        {/* Domain tree */}
        <Card className="lg:col-start-1 lg:row-start-1 h-fit">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <FolderTree className="w-4 h-4 text-gray-600" /> Domains
              </CardTitle>
              <Button size="sm" onClick={openCreateDomain}>
                <Plus className="w-4 h-4 mr-1" /> Add
              </Button>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            {isLoadingDomains ? (
              <div className="flex justify-center py-6">
                <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
              </div>
            ) : tree.length === 0 ? (
              <p className="text-sm text-gray-500 py-4">No domains yet. Create one to get started.</p>
            ) : (
              <div className="space-y-0.5">
                {tree.map(({ domain, depth }) => (
                  <button
                    key={domain.id}
                    onClick={() => setSelectedId(domain.id)}
                    style={{ paddingLeft: `${8 + depth * 16}px` }}
                    className={`w-full text-left flex items-center gap-2 pr-2 py-1.5 rounded-md text-sm transition-colors ${
                      selectedId === domain.id
                        ? 'bg-accent-soft text-accent font-medium'
                        : 'text-gray-700 hover:bg-gray-100'
                    }`}
                  >
                    <ChevronRight className="w-3 h-3 text-gray-400 shrink-0" />
                    <span className="truncate flex-1">{domain.name}</span>
                    {!domain.primary_owner && (
                      <span
                        title="No owner assigned"
                        className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0"
                      />
                    )}
                    <span className="text-xs text-gray-400">{domain.document_count ?? 0}</span>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* On desktop, selectors stay in the left rail and editors occupy the right pane. */}
        <div className="space-y-6 lg:contents">
          {showDomainForm && (
            <Card className="lg:col-start-2 lg:col-span-2 lg:row-start-1">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{editingDomain ? 'Edit Domain' : 'New Domain'}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-gray-600">Name *</label>
                    <input
                      className={inputClass}
                      value={domainForm.name}
                      onChange={(e) => setDomainForm({ ...domainForm, name: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600">Parent domain</label>
                    <select
                      className={inputClass}
                      value={domainForm.parent_id}
                      onChange={(e) => setDomainForm({ ...domainForm, parent_id: e.target.value })}
                    >
                      <option value="">— None (top level) —</option>
                      {domains
                        .filter((d) => !(editingDomain && detail && d.id === detail.id))
                        .map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.name}
                          </option>
                        ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600">Description</label>
                  <textarea
                    className={`${textareaClass} font-sans`}
                    rows={2}
                    value={domainForm.description}
                    onChange={(e) => setDomainForm({ ...domainForm, description: e.target.value })}
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-gray-600">Primary owner</label>
                    <input
                      className={inputClass}
                      value={domainForm.primary_owner}
                      onChange={(e) => setDomainForm({ ...domainForm, primary_owner: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600">Secondary owner</label>
                    <input
                      className={inputClass}
                      value={domainForm.secondary_owner}
                      onChange={(e) => setDomainForm({ ...domainForm, secondary_owner: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600">Reviewers (comma-separated)</label>
                    <input
                      className={inputClass}
                      value={domainForm.reviewers}
                      onChange={(e) => setDomainForm({ ...domainForm, reviewers: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600">Categories (comma-separated)</label>
                    <input
                      className={inputClass}
                      value={domainForm.categories}
                      onChange={(e) => setDomainForm({ ...domainForm, categories: e.target.value })}
                    />
                  </div>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button onClick={saveDomain} disabled={isSavingDomain}>
                    {isSavingDomain ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Save className="w-4 h-4 mr-1" />}
                    Save
                  </Button>
                  <Button variant="outline" onClick={() => setShowDomainForm(false)}>
                    Cancel
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {!selectedId ? (
            <Card className="lg:col-start-2 lg:col-span-2 lg:row-start-1">
              <CardContent className="py-12 text-center text-gray-500 text-sm">
                Select a domain to view its documents, or create a new one.
              </CardContent>
            </Card>
          ) : isLoadingDetail || !detail ? (
            <Card className="lg:col-start-2 lg:col-span-2 lg:row-start-1">
              <CardContent className="py-12 flex justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
              </CardContent>
            </Card>
          ) : (
            <>
              <Card className="lg:col-start-1 lg:row-start-2">
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <CardTitle className="text-lg">{detail.name}</CardTitle>
                      {detail.description && (
                        <CardDescription className="mt-1">{detail.description}</CardDescription>
                      )}
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => exportBundle({ domainIds: [detail.id] })}
                        title="Export this domain and its sub-domains"
                      >
                        <Download className="w-3.5 h-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" onClick={openEditDomain}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" onClick={deleteDomain}>
                        <Trash2 className="w-3.5 h-3.5 text-red-600" />
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="pt-0">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    <Meta label="Type" value={detail.domain_type} />
                    <Meta label="Primary owner" value={detail.primary_owner || '—'} />
                    <Meta label="Secondary owner" value={detail.secondary_owner || '—'} />
                    <Meta label="Reviewers" value={(detail.reviewers || []).join(', ') || '—'} />
                  </div>
                  {(detail.categories || []).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {detail.categories.map((c) => (
                        <span key={c} className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 text-xs">
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                  <QualityStrip detail={detail} />
                </CardContent>
              </Card>

              <Card className="lg:col-start-1 lg:row-start-3">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base flex items-center gap-2">
                      <FileText className="w-4 h-4 text-gray-600" /> Documents ({detail.documents.length})
                    </CardTitle>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={openNewDoc}>
                        <Plus className="w-4 h-4 mr-1" /> New
                      </Button>
                      <label
                        className={`inline-flex items-center h-9 px-3 rounded-md text-sm font-medium border border-gray-300 bg-white hover:bg-gray-50 cursor-pointer transition-colors ${
                          isUploading ? 'opacity-50 pointer-events-none' : ''
                        }`}
                      >
                        {isUploading ? (
                          <Loader2 className="w-4 h-4 animate-spin mr-1" />
                        ) : (
                          <Upload className="w-4 h-4 mr-1" />
                        )}
                        Upload
                        <input
                          type="file"
                          className="hidden"
                          accept=".md,.markdown,.txt,.docx,.pptx,.pdf"
                          onChange={handleUpload}
                          disabled={isUploading}
                        />
                      </label>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="pt-0 space-y-2">
                  {detail.documents.length === 0 ? (
                    <p className="text-sm text-gray-500 py-2">No documents in this domain yet.</p>
                  ) : (
                    detail.documents.map((doc) => (
                      <div
                        key={doc.id}
                        className="border border-gray-200 rounded-md p-3 flex items-start justify-between gap-3"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-sm text-gray-800 truncate">{doc.title}</span>
                            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                              {doc.doc_type}
                            </span>
                            {doc.status === 'published' ? (
                              <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-green-100 text-green-700">
                                published
                              </span>
                            ) : (
                              <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                                {doc.status}
                              </span>
                            )}
                            {isStale(doc.updated_at) && (
                              <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-orange-50 text-orange-600">
                                <AlertTriangle className="w-3 h-3" /> stale
                              </span>
                            )}
                          </div>
                          {doc.preview && (
                            <p className="text-xs text-gray-500 mt-1 line-clamp-2">{doc.preview}</p>
                          )}
                          <div className="flex items-center gap-3 flex-wrap mt-1.5 text-[11px] text-gray-400">
                            <span className="inline-flex items-center gap-1">
                              <Clock className="w-3 h-3" /> Updated {relativeTime(doc.updated_at)}
                            </span>
                            <span
                              className="inline-flex items-center gap-1"
                              title={
                                doc.last_retrieved_at
                                  ? `Last retrieved by the agent ${relativeTime(doc.last_retrieved_at)}`
                                  : 'Never retrieved by the agent yet'
                              }
                            >
                              <TrendingUp className="w-3 h-3" />{' '}
                              {doc.retrieval_count ?? 0} retrieval
                              {(doc.retrieval_count ?? 0) === 1 ? '' : 's'}
                            </span>
                            {doc.created_by && (
                              <span className="inline-flex items-center gap-1">
                                <User className="w-3 h-3" /> {doc.created_by}
                              </span>
                            )}
                            {(doc.tags || []).slice(0, 4).map((t) => (
                              <span
                                key={t}
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 text-gray-500"
                              >
                                <Tag className="w-3 h-3" /> {t}
                              </span>
                            ))}
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <Button size="sm" variant="ghost" onClick={() => openEditDoc(doc)}>
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => deleteDoc(doc)}>
                            <Trash2 className="w-3.5 h-3.5 text-red-600" />
                          </Button>
                        </div>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              {docForm && (
                <Card className="lg:col-start-2 lg:col-span-2 lg:row-start-1">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">
                      {docForm.id ? 'Edit Document' : 'New Document'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="sm:col-span-2">
                        <label className="text-xs font-medium text-gray-600">Title *</label>
                        <input
                          className={inputClass}
                          value={docForm.title}
                          onChange={(e) => setDocForm({ ...docForm, title: e.target.value })}
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600">Status</label>
                        <select
                          className={inputClass}
                          value={docForm.status}
                          onChange={(e) => setDocForm({ ...docForm, status: e.target.value })}
                        >
                          <option value="published">Published</option>
                          <option value="draft">Draft</option>
                        </select>
                      </div>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600">Markdown body</label>
                      <textarea
                        className={textareaClass}
                        rows={14}
                        value={docForm.body_markdown}
                        onChange={(e) => setDocForm({ ...docForm, body_markdown: e.target.value })}
                        placeholder="# Heading&#10;&#10;Write the context the agent should know..."
                      />
                    </div>
                    <div className="flex gap-2">
                      <Button onClick={saveDoc} disabled={isSavingDoc}>
                        {isSavingDoc ? (
                          <Loader2 className="w-4 h-4 animate-spin mr-1" />
                        ) : (
                          <Save className="w-4 h-4 mr-1" />
                        )}
                        Save
                      </Button>
                      <Button variant="outline" onClick={() => setDocForm(null)}>
                        Cancel
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </div>
      </div>

      {showImport && (
        <ImportContextCatalogModal
          onClose={() => setShowImport(false)}
          onImported={handleImported}
        />
      )}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-gray-400 uppercase tracking-wide text-[10px] font-semibold">{label}</p>
      <p className="text-gray-700 truncate mt-0.5">{value}</p>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  tone = 'neutral',
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  tone?: 'neutral' | 'good' | 'warn' | 'bad';
}) {
  const valueColor =
    tone === 'good'
      ? 'text-green-600'
      : tone === 'warn'
      ? 'text-amber-600'
      : tone === 'bad'
      ? 'text-red-600'
      : 'text-heading';
  return (
    <Card>
      <CardContent className="py-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-accent-soft text-accent flex items-center justify-center shrink-0">
          {icon}
        </div>
        <div className="min-w-0">
          <p className={`text-xl font-semibold leading-none ${valueColor}`}>{value}</p>
          <p className="text-xs text-gray-500 mt-1 truncate">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function QualityStrip({ detail }: { detail: ContextDomainDetail }) {
  const checks = domainChecks(detail);
  const passed = checks.filter((c) => c.ok).length;
  const ratio = checks.length ? passed / checks.length : 0;
  const docs = detail.documents || [];
  const published = docs.filter((d) => d.status === 'published').length;
  const drafts = docs.length - published;
  const stale = isStale(detail.updated_at);

  return (
    <div className="mt-4 pt-4 border-t border-gray-100 space-y-3">
      <div className="flex items-center gap-4 flex-wrap text-xs">
        {/* Completeness meter */}
        <div className="flex items-center gap-2">
          <Boxes className="w-4 h-4 text-gray-400" />
          <span className="text-gray-500">Completeness</span>
          <div className="w-24 h-1.5 rounded-full bg-gray-100 overflow-hidden">
            <div className={`h-full ${barColor(ratio)}`} style={{ width: `${ratio * 100}%` }} />
          </div>
          <span className={`font-semibold ${scoreColor(ratio)}`}>
            {passed}/{checks.length}
          </span>
        </div>
        <span className="text-gray-300">·</span>
        <span className="text-gray-500">
          {docs.length} docs
          {docs.length > 0 && (
            <span className="text-gray-400">
              {' '}
              ({published} published{drafts > 0 ? `, ${drafts} draft` : ''})
            </span>
          )}
        </span>
        <span className="text-gray-300">·</span>
        <span className="inline-flex items-center gap-1 text-gray-500">
          <Clock className="w-3.5 h-3.5" /> Updated {relativeTime(detail.updated_at)}
          {stale && (
            <span className="inline-flex items-center gap-1 ml-1 px-1.5 py-0.5 rounded bg-orange-50 text-orange-600">
              <AlertTriangle className="w-3 h-3" /> stale
            </span>
          )}
        </span>
      </div>

      {/* Checklist chips — green when satisfied, muted with what's missing */}
      <div className="flex flex-wrap gap-1.5">
        {checks.map((c) => (
          <span
            key={c.label}
            className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full ${
              c.ok ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400'
            }`}
          >
            {c.ok ? <CheckCircle2 className="w-3 h-3" /> : <Circle className="w-3 h-3" />}
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}
