import { useEffect, useMemo, type ReactNode } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { CheckCircle2, GitBranch, Play, Wrench, XCircle } from 'lucide-react';
import type { WorkflowGraphSpec } from '../../services/api';
import { specToFlow, type FlowNode } from '../../lib/workflowSpec';

interface PreviewNodeData {
  label: string;
  sublabel?: string;
  kind: FlowNode['kind'];
  selected?: boolean;
  onSelect?: (id: string) => void;
  nodeId: string;
}

const KIND_STYLES: Record<FlowNode['kind'], { border: string; bg: string; icon: ReactNode }> = {
  start: { border: 'border-gray-300', bg: 'bg-gray-50', icon: <Play className="w-3.5 h-3.5 text-gray-500" /> },
  gate: { border: 'border-amber-300', bg: 'bg-amber-50', icon: <GitBranch className="w-3.5 h-3.5 text-amber-600" /> },
  step: { border: 'border-blue-300', bg: 'bg-blue-50', icon: <Wrench className="w-3.5 h-3.5 text-blue-600" /> },
  complete: { border: 'border-green-300', bg: 'bg-green-50', icon: <CheckCircle2 className="w-3.5 h-3.5 text-green-600" /> },
  rejected: { border: 'border-red-300', bg: 'bg-red-50', icon: <XCircle className="w-3.5 h-3.5 text-red-600" /> },
};

function PreviewNode({ data }: NodeProps<PreviewNodeData>) {
  const s = KIND_STYLES[data.kind];
  const clickable = data.kind === 'gate' || data.kind === 'step';
  return (
    <div
      className={`rounded-md border ${s.border} ${s.bg} px-2.5 py-1.5 w-[180px] shadow-sm ${
        clickable ? 'cursor-pointer' : ''
      } ${data.selected ? 'ring-2 ring-accent' : ''}`}
    >
      <Handle type="target" position={Position.Top} style={{ background: '#cbd5e1', width: 6, height: 6 }} />
      <Handle type="source" position={Position.Bottom} style={{ background: '#cbd5e1', width: 6, height: 6 }} />
      <Handle type="source" position={Position.Right} id="r" style={{ background: '#fca5a5', width: 6, height: 6 }} />
      <div className="flex items-center gap-1.5">
        {s.icon}
        <span className="text-xs font-semibold text-gray-800 truncate">{data.label}</span>
      </div>
      {data.sublabel && (
        <div className="text-[10px] text-gray-500 truncate mt-0.5 font-mono">{data.sublabel}</div>
      )}
    </div>
  );
}

const nodeTypes = { wf: PreviewNode };

interface Props {
  spec: WorkflowGraphSpec;
  selectedStage?: string | null;
  onSelectStage?: (name: string) => void;
  height?: number | string;
}

function Flow({ spec, selectedStage, onSelectStage }: Props) {
  // Derive the desired graph from the spec. This is recomputed whenever the
  // workflow definition or selection changes.
  const derived = useMemo(() => {
    const flow = specToFlow(spec);
    const rfNodes: Node<PreviewNodeData>[] = flow.nodes.map((n) => ({
      id: n.id,
      type: 'wf',
      position: { x: n.x, y: n.y },
      data: {
        label: n.label,
        sublabel: n.sublabel,
        kind: n.kind,
        selected: selectedStage === n.id,
        onSelect: onSelectStage,
        nodeId: n.id,
      },
      draggable: false,
    }));
    const rfEdges: Edge[] = flow.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.tone === 'reject' ? 'r' : undefined,
      label: e.label,
      animated: false,
      style: { stroke: e.tone === 'reject' ? '#fca5a5' : '#cbd5e1', strokeWidth: 1.5 },
      labelStyle: { fontSize: 9, fill: '#ef4444' },
      type: 'smoothstep',
    }));
    return { nodes: rfNodes, edges: rfEdges };
  }, [spec, selectedStage, onSelectStage]);

  // ReactFlow keeps its own copy of nodes/edges. We must push the derived graph
  // into it whenever it changes, otherwise the canvas can go blank after the
  // parent re-renders with a fresh spec object (e.g. right after a save).
  const [nodes, setNodes, onNodesChange] = useNodesState(derived.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(derived.edges);
  const { fitView } = useReactFlow();

  useEffect(() => {
    setNodes(derived.nodes);
    setEdges(derived.edges);
  }, [derived, setNodes, setEdges]);

  // Re-frame the graph only when the topology (set/order of stages) changes,
  // so simply selecting a node doesn't yank the viewport around.
  const topology = useMemo(() => derived.nodes.map((n) => n.id).join('|'), [derived.nodes]);
  useEffect(() => {
    const t = window.setTimeout(() => fitView({ padding: 0.2, maxZoom: 1.1 }), 30);
    return () => window.clearTimeout(t);
  }, [topology, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onSelectStage?.(node.id)}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.3}
      maxZoom={1.5}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
    >
      <Background gap={16} size={1} color="#e5e7eb" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

export function WorkflowGraphPreview({ height = '100%', ...rest }: Props) {
  return (
    <div className="w-full bg-gray-50 border border-gray-200 rounded-lg overflow-hidden" style={{ height }}>
      <ReactFlowProvider>
        <Flow {...rest} />
      </ReactFlowProvider>
    </div>
  );
}

export default WorkflowGraphPreview;
