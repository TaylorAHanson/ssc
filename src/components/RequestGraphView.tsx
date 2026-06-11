import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { api } from '../services/api';
import type { RequestGraph } from '../services/api';
import { WorkflowGraphPreview } from './admin/WorkflowGraphPreview';

interface Props {
  requestId: string;
  /** Poll while the request is still in flight (terminal states stop polling). */
  pollMs?: number;
}

const TERMINAL = new Set(['completed', 'rejected', 'failed']);

const LEGEND: { label: string; dot: string }[] = [
  { label: 'Done', dot: 'bg-green-500' },
  { label: 'In progress', dot: 'bg-accent' },
  { label: 'Pending', dot: 'bg-gray-300' },
  { label: 'Rejected', dot: 'bg-red-500' },
];

/** Live visual runner: the authored workflow graph with each node annotated by
 *  its real run status (done / current / pending / rejected). */
export function RequestGraphView({ requestId, pollMs = 5000 }: Props) {
  const [graph, setGraph] = useState<RequestGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const g = await api.getRequestGraph(requestId);
      setGraph(g);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workflow graph');
    } finally {
      setLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll until the request reaches a terminal state.
  useEffect(() => {
    if (!graph || TERMINAL.has(graph.status)) return;
    const t = window.setInterval(load, pollMs);
    return () => window.clearInterval(t);
  }, [graph, load, pollMs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  if (error) {
    return <div className="text-sm text-red-600 py-6 text-center">{error}</div>;
  }
  if (!graph || !graph.graph_spec?.stages?.length) {
    return (
      <div className="text-sm text-gray-400 py-10 text-center">
        No workflow graph is available for this request type.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 text-xs text-gray-500">
          {LEGEND.map((l) => (
            <span key={l.label} className="inline-flex items-center gap-1.5">
              <span className={`w-2.5 h-2.5 rounded-full ${l.dot}`} />
              {l.label}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {!TERMINAL.has(graph.status) && (
            <span className="text-[11px] text-gray-400 inline-flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" /> live
            </span>
          )}
          <button
            type="button"
            onClick={load}
            className="text-gray-400 hover:text-accent p-1"
            title="Refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      <WorkflowGraphPreview spec={graph.graph_spec} nodeStates={graph.node_states} height={420} />
    </div>
  );
}

export default RequestGraphView;
