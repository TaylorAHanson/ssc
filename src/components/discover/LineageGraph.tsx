import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactFlow, {
    Background,
    Controls,
    Handle,
    MiniMap,
    Position,
    type Edge,
    type Node,
    type NodeProps,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
    ChevronLeft,
    ChevronRight,
    ExternalLink,
    Loader2,
    Lock,
    Star,
    TableIcon,
} from 'lucide-react';
import { api } from '../../services/api';
import { catalogExplorerUrl } from '../../lib/databricksLinks';

// ---------------------------------------------------------------------------
// Types & helpers
// ---------------------------------------------------------------------------

export interface LineageSeedTable {
    /** Fully qualified name: catalog.schema.table */
    fqn: string;
    /** Optional display label; defaults to the table portion of the FQN. */
    displayName?: string;
    /** Optional classification badge to render (e.g. PII). */
    classification?: string | null;
    /** Optional pre-known upstream neighbors (FQNs) from the contract YAML. */
    upstreams?: string[];
    /** Optional pre-known downstream neighbors (FQNs) from the contract YAML. */
    downstreams?: string[];
}

export interface LineageGraphProps {
    seedTables: LineageSeedTable[];
    workspaceUrl: string;
    /** Render the graph in a tall container (e.g. a modal tab). */
    height?: string | number;
}

function splitFqn(fqn: string): { catalog?: string; schema?: string; table?: string } {
    const parts = (fqn || '').split('.');
    if (parts.length !== 3) return {};
    return { catalog: parts[0], schema: parts[1], table: parts[2] };
}

function shortName(fqn: string): string {
    const { table } = splitFqn(fqn);
    return table || fqn;
}

// Layout constants (kept inline so we don't pull in dagre or another dep).
const COL_WIDTH = 280;
const ROW_HEIGHT = 110;

// ---------------------------------------------------------------------------
// Custom node component
// ---------------------------------------------------------------------------

interface LineageNodeData {
    fqn: string;
    label: string;
    catalog?: string;
    schema?: string;
    table?: string;
    role: 'primary' | 'neighbor';
    classification?: string | null;
    workspaceUrl: string;
    /** Whether upstream side has been expanded already (don't show button). */
    upstreamLoaded: boolean;
    /** Whether downstream side has been expanded already. */
    downstreamLoaded: boolean;
    isLoading: boolean;
    onExpand: (fqn: string, direction: 'upstream' | 'downstream') => void;
}

function LineageNode({ data }: NodeProps<LineageNodeData>) {
    const isPrimary = data.role === 'primary';
    const href = catalogExplorerUrl(data.workspaceUrl, data.catalog, data.schema, data.table);

    return (
        <div
            className={`rounded-lg border bg-white shadow-sm w-[240px] text-left transition-shadow ${
                isPrimary
                    ? 'border-primary/60 ring-2 ring-primary/20'
                    : 'border-gray-200 hover:shadow-md'
            }`}
        >
            <Handle
                type="target"
                position={Position.Left}
                style={{ background: '#9ca3af', width: 8, height: 8 }}
            />
            <Handle
                type="source"
                position={Position.Right}
                style={{ background: '#9ca3af', width: 8, height: 8 }}
            />

            <div className="px-3 py-2 border-b border-gray-100 flex items-start gap-2">
                <TableIcon
                    className={`w-4 h-4 mt-0.5 shrink-0 ${
                        isPrimary ? 'text-primary' : 'text-gray-400'
                    }`}
                />
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                        <span className="text-sm font-semibold text-gray-900 truncate" title={data.label}>
                            {data.label}
                        </span>
                        {isPrimary && (
                            <Star className="w-3 h-3 text-amber-500 shrink-0" aria-label="Selected dataset table" />
                        )}
                    </div>
                    <div
                        className="font-mono text-[10px] text-gray-500 truncate"
                        title={data.fqn}
                    >
                        {data.fqn}
                    </div>
                </div>
                {data.classification && (
                    <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-rose-50 text-rose-700 border border-rose-100 uppercase inline-flex items-center gap-0.5 shrink-0">
                        <Lock className="w-2.5 h-2.5" /> {data.classification}
                    </span>
                )}
            </div>

            <div className="px-2 py-1.5 flex items-center justify-between gap-1 text-[11px]">
                <button
                    type="button"
                    disabled={data.upstreamLoaded || data.isLoading}
                    onClick={(e) => {
                        e.stopPropagation();
                        data.onExpand(data.fqn, 'upstream');
                    }}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-gray-600 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-default"
                    title={data.upstreamLoaded ? 'Upstream loaded' : 'Show tables that feed this one'}
                >
                    {data.isLoading ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                        <ChevronLeft className="w-3 h-3" />
                    )}
                    <span>Sources</span>
                </button>

                {href ? (
                    <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-gray-500 hover:text-primary hover:bg-gray-50"
                        title="Open in Catalog Explorer"
                        aria-label="Open in Catalog Explorer"
                    >
                        <ExternalLink className="w-3 h-3" />
                    </a>
                ) : (
                    <span />
                )}

                <button
                    type="button"
                    disabled={data.downstreamLoaded || data.isLoading}
                    onClick={(e) => {
                        e.stopPropagation();
                        data.onExpand(data.fqn, 'downstream');
                    }}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-gray-600 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-default"
                    title={data.downstreamLoaded ? 'Downstream loaded' : 'Show tables that consume this one'}
                >
                    <span>Consumers</span>
                    {data.isLoading ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                        <ChevronRight className="w-3 h-3" />
                    )}
                </button>
            </div>
        </div>
    );
}

const nodeTypes = { lineage: LineageNode };

// ---------------------------------------------------------------------------
// Internal model: track column placement so layout works as we expand
// ---------------------------------------------------------------------------

interface InternalNode {
    fqn: string;
    column: number; // negative = upstream, 0 = primary, positive = downstream
    role: 'primary' | 'neighbor';
    classification?: string | null;
    upstreamLoaded: boolean;
    downstreamLoaded: boolean;
}

function layoutNodes(
    internal: Map<string, InternalNode>,
    isLoadingFqn: string | null,
    workspaceUrl: string,
    onExpand: (fqn: string, dir: 'upstream' | 'downstream') => void,
): Node<LineageNodeData>[] {
    // Group by column to compute Y positions.
    const byColumn = new Map<number, InternalNode[]>();
    for (const n of internal.values()) {
        const list = byColumn.get(n.column) ?? [];
        list.push(n);
        byColumn.set(n.column, list);
    }

    const nodes: Node<LineageNodeData>[] = [];
    for (const [col, list] of byColumn.entries()) {
        list.sort((a, b) => a.fqn.localeCompare(b.fqn));
        const colHeight = list.length * ROW_HEIGHT;
        list.forEach((n, idx) => {
            const { catalog, schema, table } = splitFqn(n.fqn);
            const y = idx * ROW_HEIGHT - colHeight / 2;
            const x = col * COL_WIDTH;
            nodes.push({
                id: n.fqn,
                type: 'lineage',
                position: { x, y },
                draggable: true,
                data: {
                    fqn: n.fqn,
                    label: shortName(n.fqn),
                    catalog,
                    schema,
                    table,
                    role: n.role,
                    classification: n.classification ?? null,
                    workspaceUrl,
                    upstreamLoaded: n.upstreamLoaded,
                    downstreamLoaded: n.downstreamLoaded,
                    isLoading: isLoadingFqn === n.fqn,
                    onExpand,
                },
            });
        });
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function LineageGraph({ seedTables, workspaceUrl, height = '100%' }: LineageGraphProps) {
    // Normalize seed tables (skip empty/duplicate FQNs).
    const seeds = useMemo(() => {
        const seen = new Set<string>();
        return seedTables.filter((s) => {
            if (!s.fqn || seen.has(s.fqn)) return false;
            seen.add(s.fqn);
            return true;
        });
    }, [seedTables]);

    const [internal, setInternal] = useState<Map<string, InternalNode>>(() => new Map());
    const [edges, setEdges] = useState<Edge[]>([]);
    const [loadingFqn, setLoadingFqn] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Initial seed: place primaries at column 0 plus any pre-known neighbors.
    useEffect(() => {
        const next = new Map<string, InternalNode>();
        const seedEdges: Edge[] = [];

        for (const s of seeds) {
            next.set(s.fqn, {
                fqn: s.fqn,
                column: 0,
                role: 'primary',
                classification: s.classification ?? null,
                upstreamLoaded: !!(s.upstreams && s.upstreams.length === 0),
                downstreamLoaded: !!(s.downstreams && s.downstreams.length === 0),
            });
        }

        for (const s of seeds) {
            (s.upstreams ?? []).forEach((up) => {
                if (!next.has(up)) {
                    next.set(up, {
                        fqn: up,
                        column: -1,
                        role: 'neighbor',
                        upstreamLoaded: false,
                        downstreamLoaded: true, // we already know it points to s.fqn
                    });
                }
                seedEdges.push({
                    id: `${up}->${s.fqn}`,
                    source: up,
                    target: s.fqn,
                    animated: false,
                });
            });
            (s.downstreams ?? []).forEach((down) => {
                if (!next.has(down)) {
                    next.set(down, {
                        fqn: down,
                        column: 1,
                        role: 'neighbor',
                        upstreamLoaded: true, // we already know s.fqn points to it
                        downstreamLoaded: false,
                    });
                }
                seedEdges.push({
                    id: `${s.fqn}->${down}`,
                    source: s.fqn,
                    target: down,
                    animated: false,
                });
            });
            // If user provided arrays, mark seed as loaded for that side so
            // we don't double-fetch the same hop.
            if (s.upstreams) {
                const cur = next.get(s.fqn)!;
                cur.upstreamLoaded = true;
            }
            if (s.downstreams) {
                const cur = next.get(s.fqn)!;
                cur.downstreamLoaded = true;
            }
        }

        setInternal(next);
        setEdges(seedEdges);
        setError(null);
    }, [seeds]);

    const handleExpand = useCallback(
        async (fqn: string, direction: 'upstream' | 'downstream') => {
            setLoadingFqn(fqn);
            setError(null);
            try {
                const resp = await api.getTableLineage(fqn);
                setInternal((prev) => {
                    const next = new Map(prev);
                    const center = next.get(fqn);
                    if (!center) return prev;

                    const list = direction === 'upstream' ? resp.upstreams : resp.downstreams;
                    const targetColumn = center.column + (direction === 'upstream' ? -1 : 1);

                    list.forEach((nb) => {
                        if (!nb?.name) return;
                        if (!next.has(nb.name)) {
                            next.set(nb.name, {
                                fqn: nb.name,
                                column: targetColumn,
                                role: 'neighbor',
                                upstreamLoaded: direction === 'downstream',
                                downstreamLoaded: direction === 'upstream',
                            });
                        }
                    });

                    const updated = { ...center };
                    if (direction === 'upstream') updated.upstreamLoaded = true;
                    else updated.downstreamLoaded = true;
                    next.set(fqn, updated);
                    return next;
                });
                setEdges((prev) => {
                    const ids = new Set(prev.map((e) => e.id));
                    const list = direction === 'upstream' ? resp.upstreams : resp.downstreams;
                    const additions: Edge[] = [];
                    list.forEach((nb) => {
                        if (!nb?.name) return;
                        const id = direction === 'upstream' ? `${nb.name}->${fqn}` : `${fqn}->${nb.name}`;
                        if (ids.has(id)) return;
                        additions.push({
                            id,
                            source: direction === 'upstream' ? nb.name : fqn,
                            target: direction === 'upstream' ? fqn : nb.name,
                            animated: false,
                        });
                    });
                    return [...prev, ...additions];
                });
            } catch (e) {
                setError(e instanceof Error ? e.message : 'Failed to fetch lineage.');
            } finally {
                setLoadingFqn(null);
            }
        },
        [],
    );

    // For a single, fully-unexpanded seed (typical for a UC table opened from
    // Discover), auto-fetch on mount so the user sees a useful graph without
    // an extra click. Datasets with multiple seeds are left to manual expand
    // to avoid a burst of API calls on open.
    useEffect(() => {
        if (seeds.length !== 1) return;
        const seed = seeds[0];
        const hasPreloaded =
            (Array.isArray(seed.upstreams) && seed.upstreams.length > 0) ||
            (Array.isArray(seed.downstreams) && seed.downstreams.length > 0);
        if (hasPreloaded) return;
        // Fetch both directions; the API returns both in one response so the
        // second call is cheap and keeps the state-machine logic uniform.
        void handleExpand(seed.fqn, 'upstream');
        void handleExpand(seed.fqn, 'downstream');
    }, [seeds, handleExpand]);

    const nodes = useMemo(
        () => layoutNodes(internal, loadingFqn, workspaceUrl, handleExpand),
        [internal, loadingFqn, workspaceUrl, handleExpand],
    );

    const isEmpty = nodes.length === 0;

    return (
        <div className="w-full" style={{ height }}>
            {error && (
                <div className="mb-2 text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-md px-3 py-2">
                    {error}
                </div>
            )}
            {isEmpty ? (
                <div className="h-full flex items-center justify-center text-sm text-gray-500 bg-gray-50 border border-dashed border-gray-200 rounded-lg">
                    No lineage information available.
                </div>
            ) : (
                <div className="w-full h-full bg-gray-50 border border-gray-200 rounded-lg overflow-hidden">
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        nodeTypes={nodeTypes}
                        fitView
                        fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
                        proOptions={{ hideAttribution: true }}
                        defaultEdgeOptions={{
                            type: 'smoothstep',
                            style: { stroke: '#9ca3af', strokeWidth: 1.5 },
                        }}
                        minZoom={0.2}
                        maxZoom={1.5}
                    >
                        <Background gap={16} size={1} color="#e5e7eb" />
                        <Controls showInteractive={false} />
                        <MiniMap
                            pannable
                            zoomable
                            nodeStrokeWidth={3}
                            nodeColor={(n) =>
                                (n.data as LineageNodeData)?.role === 'primary' ? '#FF3621' : '#cbd5e1'
                            }
                            maskColor="rgba(255,255,255,0.6)"
                        />
                    </ReactFlow>
                </div>
            )}
        </div>
    );
}

export default LineageGraph;
