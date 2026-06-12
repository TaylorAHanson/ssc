import { useCallback, useMemo, useState } from 'react';
import {
  CheckCircle2,
  GitBranch,
  GripVertical,
  Loader2,
  Play,
  Plus,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type {
  GateApprover,
  GateType,
  SpecExpr,
  WorkflowGateStage,
  WorkflowGraphSpec,
  WorkflowStage,
  WorkflowStepStage,
  WorkflowTool,
} from '../../services/api';
import {
  GATE_TYPES,
  argValueToExpr,
  autoApproveToModel,
  exprToArgValue,
  modelToAutoApprove,
  newGate,
  newStep,
  type ArgKind,
  type ArgValue,
  type AutoApproveModel,
  type Condition,
} from '../../lib/workflowSpec';
import WorkflowGraphPreview from './WorkflowGraphPreview';
import WorkflowTestModal from './WorkflowTestModal';
import { HelpTip, LabelWithHelp, AskAgentHint } from '../ui/help-tip';

// `inputBase` has no width so it can be combined with flex/explicit widths
// without the `w-full` conflict that otherwise collapses flex children.
const inputBase =
  'border border-gray-300 rounded-md h-9 px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent';
const inputClass = `w-full ${inputBase}`;
const labelClass = 'block text-[11px] font-medium text-gray-600 mb-1';

interface Props {
  spec: WorkflowGraphSpec;
  tools: WorkflowTool[];
  onChange: (spec: WorkflowGraphSpec) => void;
  /** Opens the in-page authoring assistant panel (instead of a new chat tab). */
  onAskAgent?: () => void;
}

export function WorkflowEditor({ spec, tools, onChange, onAskAgent }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(
    spec.stages.length ? 0 : null,
  );
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{ ok: boolean; msg: string } | null>(null);
  const [showTest, setShowTest] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dropIdx, setDropIdx] = useState<number | null>(null);

  const defaultTool = tools[0]?.name || 'send_notification';
  const stages = spec.stages;

  const setStages = (next: WorkflowStage[]) => {
    onChange({ ...spec, stages: next });
    setValidation(null);
  };

  const updateStage = (idx: number, patch: Partial<WorkflowStage>) => {
    const next = stages.map((s, i) => (i === idx ? ({ ...s, ...patch } as WorkflowStage) : s));
    setStages(next);
  };

  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= stages.length) return;
    const next = [...stages];
    [next[idx], next[j]] = [next[j], next[idx]];
    setStages(next);
    setSelectedIdx(j);
  };

  const remove = (idx: number) => {
    const next = stages.filter((_, i) => i !== idx);
    setStages(next);
    setSelectedIdx(next.length ? Math.max(0, idx - 1) : null);
  };

  const addGate = () => {
    const next = [...stages, newGate(stages.length + 1)];
    setStages(next);
    setSelectedIdx(next.length - 1);
  };

  const addStep = () => {
    const next = [...stages, newStep(stages.length + 1, defaultTool)];
    setStages(next);
    setSelectedIdx(next.length - 1);
  };

  // Stable identity so the graph preview's memo/effects don't churn (which can
  // blank the canvas). Resolve the name against the latest stages at call time.
  const selectByName = useCallback(
    (name: string) => {
      const idx = spec.stages.findIndex((s) => s.name === name);
      if (idx >= 0) setSelectedIdx(idx);
    },
    [spec.stages],
  );

  const reorder = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0) return;
    const next = [...stages];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setStages(next);
    setSelectedIdx(to);
  };

  const runValidate = async () => {
    setValidating(true);
    setValidation(null);
    try {
      const res = await api.validateSpec(spec);
      const warnings = res.warnings ?? [];
      if (warnings.length > 0) {
        setValidation({
          ok: false,
          msg: `Valid structure, but ${warnings.length} arg warning${warnings.length === 1 ? '' : 's'} (won't reach the tool): ${warnings.join('; ')}`,
        });
      } else {
        setValidation({ ok: true, msg: 'Valid workflow — ready to publish.' });
      }
    } catch (e) {
      setValidation({ ok: false, msg: e instanceof Error ? e.message : 'Invalid workflow' });
    } finally {
      setValidating(false);
    }
  };

  const selected = selectedIdx !== null ? stages[selectedIdx] : null;
  // Gate types available as step approvals = gate types appearing before the step.
  const approvalOptions = useMemo(() => {
    if (selectedIdx === null) return [];
    const set = new Set<string>();
    for (let i = 0; i < selectedIdx; i += 1) {
      const s = stages[i];
      if (s.kind === 'gate') set.add(s.type);
    }
    return Array.from(set);
  }, [stages, selectedIdx]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={addGate}>
            <GitBranch className="w-3.5 h-3.5 mr-1" /> Add gate
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={addStep}>
            <Wrench className="w-3.5 h-3.5 mr-1" /> Add step
          </Button>
          <HelpTip text="A workflow runs its stages top to bottom. Add a gate for a human/event approval the request pauses on, or a step to run one governed tool. Drag to reorder." />
          <AskAgentHint className="ml-1" onClick={onAskAgent} label="Ask the agent" />
        </div>
        <div className="flex items-center gap-3">
          {validation && (
            <span
              className={`text-xs inline-flex items-center gap-1 ${
                validation.ok ? 'text-green-700' : 'text-red-600'
              }`}
            >
              {validation.ok ? (
                <CheckCircle2 className="w-3.5 h-3.5" />
              ) : (
                <X className="w-3.5 h-3.5" />
              )}
              {validation.msg}
            </span>
          )}
          <Button type="button" variant="outline" size="sm" onClick={runValidate} disabled={validating}>
            {validating ? (
              <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            ) : (
              <ShieldCheck className="w-3.5 h-3.5 mr-1" />
            )}
            Validate
          </Button>
          <Button type="button" size="sm" onClick={() => setShowTest(true)} disabled={stages.length === 0}>
            <Play className="w-3.5 h-3.5 mr-1" /> Test
          </Button>
          <HelpTip text="Validate checks the structure (gate types, real tools, well-formed expressions). Test dry-runs the workflow against a sample request — projecting which gates auto-approve and the exact args each step gets — without running anything." />
        </div>
      </div>

      {/* Visual zone: stage list + canvas. Drag the bottom-right handle to resize. */}
      <div
        className="flex flex-col lg:flex-row gap-3 resize-y overflow-hidden min-h-[260px] rounded-md"
        style={{ height: 420 }}
      >
        {/* Stage list */}
        <div className="lg:w-60 shrink-0 lg:h-full overflow-y-auto border border-gray-200 rounded-md p-2 space-y-1">
          <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1 px-1">
            Stages ({stages.length})
          </div>
          {stages.length === 0 && (
            <div className="text-xs text-gray-400 border border-dashed border-gray-200 rounded-md p-3 text-center">
              No stages yet. Add a gate or step to begin.
            </div>
          )}
          {stages.map((s, idx) => (
            <div
              key={idx}
              draggable
              onClick={() => setSelectedIdx(idx)}
              onDragStart={() => setDragIdx(idx)}
              onDragOver={(e) => {
                e.preventDefault();
                if (dropIdx !== idx) setDropIdx(idx);
              }}
              onDragEnd={() => {
                setDragIdx(null);
                setDropIdx(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragIdx !== null) reorder(dragIdx, idx);
                setDragIdx(null);
                setDropIdx(null);
              }}
              className={`w-full text-left flex items-center gap-1.5 rounded-md px-2 py-1.5 border cursor-pointer ${
                selectedIdx === idx ? 'border-accent bg-accent/5' : 'border-transparent hover:bg-gray-50'
              } ${dropIdx === idx && dragIdx !== null && dragIdx !== idx ? 'border-t-2 border-t-accent' : ''} ${
                dragIdx === idx ? 'opacity-40' : ''
              }`}
            >
              <GripVertical className="w-3.5 h-3.5 text-gray-300 shrink-0 cursor-grab" />
              <span className="text-[10px] text-gray-400 w-4">{idx + 1}</span>
              {s.kind === 'gate' ? (
                <GitBranch className="w-3.5 h-3.5 text-amber-600 shrink-0" />
              ) : (
                <Wrench className="w-3.5 h-3.5 text-blue-600 shrink-0" />
              )}
              <span className="text-sm truncate flex-1">{s.name || '(unnamed)'}</span>
              <span className="text-[10px] text-gray-400 truncate max-w-[64px]">
                {s.kind === 'gate' ? s.type : (s as WorkflowStepStage).tool}
              </span>
            </div>
          ))}
        </div>

        {/* Canvas */}
        <div className="flex-1 h-[260px] lg:h-full min-w-0">
          <WorkflowGraphPreview
            spec={spec}
            selectedStage={selected?.name ?? null}
            onSelectStage={selectByName}
            height="100%"
          />
        </div>
      </div>

      {/* Stage inspector — full width below the canvas so fields have room. */}
      <div>
        {selected === null ? (
          <div className="text-sm text-gray-400 border border-dashed border-gray-200 rounded-md p-6 text-center">
            Select a stage in the list above or click a node in the canvas to edit it.
          </div>
        ) : (
          <div className="border border-gray-200 rounded-md">
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5 bg-gray-50/60 rounded-t-md">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                {selected.kind === 'gate' ? (
                  <GitBranch className="w-4 h-4 text-amber-600" />
                ) : (
                  <Wrench className="w-4 h-4 text-blue-600" />
                )}
                {selected.kind === 'gate' ? 'Gate' : 'Provision step'}
                <span className="text-xs font-normal text-gray-400">
                  · stage {selectedIdx! + 1} of {stages.length}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <Button type="button" variant="ghost" size="sm" onClick={() => move(selectedIdx!, -1)}
                  disabled={selectedIdx === 0} title="Move earlier">↑</Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => move(selectedIdx!, 1)}
                  disabled={selectedIdx === stages.length - 1} title="Move later">↓</Button>
                <Button type="button" variant="ghost" size="sm"
                  className="text-red-600 hover:bg-red-50" onClick={() => remove(selectedIdx!)} title="Delete stage">
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>

            <div className="p-4 space-y-4 max-w-3xl">
              <div>
                <LabelWithHelp className={labelClass} help="Unique internal id for this stage. Used by the timeline and live graph. Can't be one of the reserved names: pending, complete, completed, rejected.">
                  Stage name (internal id)
                </LabelWithHelp>
                <input
                  className={inputClass}
                  value={selected.name}
                  onChange={(e) => updateStage(selectedIdx!, { name: e.target.value })}
                />
              </div>

              {selected.kind === 'gate' ? (
                <GateForm
                  gate={selected}
                  onChange={(patch) => updateStage(selectedIdx!, patch)}
                />
              ) : (
                <StepForm
                  step={selected}
                  tools={tools}
                  approvalOptions={approvalOptions}
                  onChange={(patch) => updateStage(selectedIdx!, patch)}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {showTest && <WorkflowTestModal spec={spec} onClose={() => setShowTest(false)} />}
    </div>
  );
}

// --------------------------------------------------------------------------
// Gate form
// --------------------------------------------------------------------------
const ADVANCED_AUTO_APPROVE_SEED = JSON.stringify(
  { $bool: { $var: 'is_auto_approve' } },
  null,
  2,
);

function GateForm({
  gate,
  onChange,
}: {
  gate: WorkflowGateStage;
  onChange: (patch: Partial<WorkflowGateStage>) => void;
}) {
  const model = autoApproveToModel(gate.auto_approve);
  // Local draft for the advanced JSON editor so invalid intermediate text isn't
  // discarded; we only commit to the spec when it parses.
  const [advDraft, setAdvDraft] = useState<string | null>(null);
  const [advError, setAdvError] = useState<string | null>(null);

  const setModel = (m: AutoApproveModel) => {
    try {
      onChange({ auto_approve: modelToAutoApprove(m) });
    } catch {
      // invalid advanced JSON — keep editing, validation will flag it on save
      onChange({ auto_approve: gate.auto_approve });
    }
  };

  const onAdvancedChange = (text: string) => {
    setAdvDraft(text);
    try {
      onChange({ auto_approve: JSON.parse(text) });
      setAdvError(null);
    } catch {
      setAdvError('Invalid JSON');
    }
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <LabelWithHelp className={labelClass} help="The kind of gate the request pauses on — NOT a group name. manager / platform_admin / data_owner are human approvals; training waits for a completed training, pr_merge for a merged PR, and children for all spawned child requests. To send approval to a specific group, pick a human type and set the Approver group below.">
            Gate type
          </LabelWithHelp>
          <select
            className={inputClass}
            value={gate.type}
            onChange={(e) => onChange({ type: e.target.value as GateType })}
          >
            {GATE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <LabelWithHelp className={labelClass} help="Optional status label shown on the request while it's paused at this gate (e.g. 'manager_approval'). Purely for display in the request list/timeline.">
            Status while waiting
          </LabelWithHelp>
          <input
            className={inputClass}
            value={gate.waiting_status || ''}
            placeholder="manager_approval"
            onChange={(e) => onChange({ waiting_status: e.target.value })}
          />
        </div>
      </div>

      <GateApproverField gate={gate} onChange={onChange} />

      <div>
        <LabelWithHelp className={labelClass} help="Optionally skip the human approval when a condition holds (e.g. low-risk scope). 'Never' always requires approval; 'When a condition is met' builds a simple rule; 'Advanced' is a raw expression for complex logic.">
          Auto-approve (skip this gate when…)
        </LabelWithHelp>
        <select
          className={inputClass}
          value={model.mode}
          onChange={(e) => {
            const mode = e.target.value as AutoApproveModel['mode'];
            setAdvDraft(null);
            setAdvError(null);
            if (mode === 'always') setModel({ mode, conditions: [] });
            else if (mode === 'conditions')
              setModel({ mode, conditions: model.conditions.length ? model.conditions : [{ field: '', op: 'truthy' }] });
            else {
              const seed = model.raw || ADVANCED_AUTO_APPROVE_SEED;
              setAdvDraft(seed);
              setModel({ mode, conditions: [], raw: seed });
            }
          }}
        >
          <option value="always">Never — always require approval</option>
          <option value="conditions">When a condition is met</option>
          <option value="advanced">Advanced (raw expression)</option>
        </select>
      </div>

      {model.mode === 'conditions' && (
        <ConditionList
          conditions={model.conditions}
          onChange={(conditions) => setModel({ mode: 'conditions', conditions })}
        />
      )}
      {model.mode === 'advanced' && (
        <div>
          <textarea
            className="w-full border border-gray-300 rounded-md p-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-accent"
            rows={5}
            value={advDraft ?? model.raw ?? ''}
            onChange={(e) => onAdvancedChange(e.target.value)}
          />
          {advError && <div className="text-[11px] text-red-500 mt-1">{advError}</div>}
        </div>
      )}
    </>
  );
}

// Human gate types where routing an approval to a specific group makes sense.
const HUMAN_GATE_TYPES: GateType[] = ['manager', 'platform_admin', 'data_owner'];

type ApproverSource = 'default' | 'group' | 'approver_group_tag';

function GateApproverField({
  gate,
  onChange,
}: {
  gate: WorkflowGateStage;
  onChange: (patch: Partial<WorkflowGateStage>) => void;
}) {
  const isHuman = HUMAN_GATE_TYPES.includes(gate.type);
  const source: ApproverSource = gate.approver?.source ?? 'default';

  if (!isHuman) {
    return (
      <div className="text-[11px] text-gray-400">
        Approver routing doesn't apply to a <span className="font-mono">{gate.type}</span> gate — it
        proceeds on the event, not a person's approval.
      </div>
    );
  }

  const setSource = (next: ApproverSource) => {
    if (next === 'default') {
      onChange({ approver: null });
    } else if (next === 'group') {
      const group = gate.approver?.source === 'group' ? gate.approver.group : '';
      onChange({ approver: { source: 'group', group } });
    } else {
      const fallback =
        gate.approver?.source === 'approver_group_tag'
          ? gate.approver.fallback_to_owner ?? true
          : true;
      onChange({ approver: { source: 'approver_group_tag', fallback_to_owner: fallback } });
    }
  };

  const setApprover = (next: GateApprover) => onChange({ approver: next });

  return (
    <div className="rounded-md border border-gray-200 bg-gray-50/40 p-3 space-y-3">
      <div>
        <LabelWithHelp
          className={labelClass}
          help="Who can approve this human gate. 'Default' uses the gate type's built-in routing (e.g. manager → the requester's manager). 'Specific group' sends it to any member of a group you name. 'From data's approver_group tag' resolves the group from the Unity Catalog approver_group tag on the requested assets."
        >
          Approver group
        </LabelWithHelp>
        <select
          className={inputClass}
          value={source}
          onChange={(e) => setSource(e.target.value as ApproverSource)}
        >
          <option value="default">Default for this gate type</option>
          <option value="group">Specific group</option>
          <option value="approver_group_tag">From data's approver_group tag</option>
        </select>
      </div>

      {source === 'group' && (
        <div>
          <LabelWithHelp
            className={labelClass}
            help="Any member of this group (a Databricks account group or role name) can approve. The pending approval task is assigned to this group."
          >
            Group name
          </LabelWithHelp>
          <input
            className={inputClass}
            value={gate.approver?.source === 'group' ? gate.approver.group : ''}
            placeholder="e.g. edh_training_admin"
            onChange={(e) => setApprover({ source: 'group', group: e.target.value })}
          />
        </div>
      )}

      {source === 'approver_group_tag' && (
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={
              gate.approver?.source === 'approver_group_tag'
                ? gate.approver.fallback_to_owner ?? true
                : true
            }
            onChange={(e) =>
              setApprover({ source: 'approver_group_tag', fallback_to_owner: e.target.checked })
            }
          />
          Fall back to the data owner when no <span className="font-mono">approver_group</span> tag is
          set
        </label>
      )}
    </div>
  );
}

function ConditionList({
  conditions,
  onChange,
  heading = 'Auto-approve if ANY of these are true:',
}: {
  conditions: Condition[];
  onChange: (c: Condition[]) => void;
  heading?: string;
}) {
  const update = (i: number, patch: Partial<Condition>) =>
    onChange(conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  return (
    <div className="space-y-2 pl-2 border-l-2 border-amber-200">
      <div className="text-[11px] text-gray-500">{heading}</div>
      {conditions.map((c, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400 shrink-0">field</span>
          <input
            className={`${inputBase} flex-1 min-w-0`}
            placeholder="request field (e.g. scope, tier)"
            value={c.field}
            onChange={(e) => update(i, { field: e.target.value })}
          />
          <select
            className={`${inputBase} w-40 shrink-0`}
            value={c.op}
            onChange={(e) => update(i, { op: e.target.value as Condition['op'] })}
          >
            <option value="truthy">is set / true</option>
            <option value="falsy">is empty / false</option>
            <option value="equals">equals</option>
          </select>
          {c.op === 'equals' && (
            <input
              className={`${inputBase} flex-1 min-w-0`}
              placeholder="value"
              value={c.value || ''}
              onChange={(e) => update(i, { value: e.target.value })}
            />
          )}
          <button type="button" className="text-gray-400 hover:text-red-500"
            onClick={() => onChange(conditions.filter((_, idx) => idx !== i))}>
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
      <Button type="button" variant="ghost" size="sm"
        onClick={() => onChange([...conditions, { field: '', op: 'truthy' }])}>
        <Plus className="w-3.5 h-3.5 mr-1" /> Add condition
      </Button>
    </div>
  );
}

// --------------------------------------------------------------------------
// Conditional "run this step only when…" editor (reuses the predicate model).
// run_if === null means the step always runs.
// --------------------------------------------------------------------------
const ADVANCED_RUN_IF_SEED = JSON.stringify({ $eq: [{ $var: 'tier' }, 'high'] }, null, 2);

function RunIfEditor({
  value,
  onChange,
}: {
  value: SpecExpr | null | undefined;
  onChange: (runIf: SpecExpr | null) => void;
}) {
  const model = autoApproveToModel(value);
  const [advDraft, setAdvDraft] = useState<string | null>(null);
  const [advError, setAdvError] = useState<string | null>(null);

  const setModel = (m: AutoApproveModel) => {
    try {
      onChange(modelToAutoApprove(m));
    } catch {
      onChange(value ?? null);
    }
  };

  const onAdvancedChange = (text: string) => {
    setAdvDraft(text);
    try {
      onChange(JSON.parse(text));
      setAdvError(null);
    } catch {
      setAdvError('Invalid JSON');
    }
  };

  return (
    <div>
      <LabelWithHelp className={labelClass} help="Conditional branching: run this step only when a rule about the request holds (e.g. tier equals 'high'). 'Always' runs it every time; 'Only when a condition is met' builds a simple rule; 'Advanced' is a raw expression. Skipped steps don't run their tool and don't block the workflow.">
        Run this step…
      </LabelWithHelp>
      <select
        className={inputClass}
        value={model.mode}
        onChange={(e) => {
          const mode = e.target.value as AutoApproveModel['mode'];
          setAdvDraft(null);
          setAdvError(null);
          if (mode === 'always') setModel({ mode, conditions: [] });
          else if (mode === 'conditions')
            setModel({ mode, conditions: model.conditions.length ? model.conditions : [{ field: '', op: 'truthy' }] });
          else {
            const seed = model.raw || ADVANCED_RUN_IF_SEED;
            setAdvDraft(seed);
            setModel({ mode, conditions: [], raw: seed });
          }
        }}
      >
        <option value="always">Always — run every time</option>
        <option value="conditions">Only when a condition is met</option>
        <option value="advanced">Advanced (raw expression)</option>
      </select>

      {model.mode === 'conditions' && (
        <ConditionList
          heading="Run this step if ANY of these are true:"
          conditions={model.conditions}
          onChange={(conditions) => setModel({ mode: 'conditions', conditions })}
        />
      )}
      {model.mode === 'advanced' && (
        <div className="mt-2">
          <textarea
            className="w-full border border-gray-300 rounded-md p-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-accent"
            rows={5}
            value={advDraft ?? model.raw ?? ''}
            onChange={(e) => onAdvancedChange(e.target.value)}
          />
          {advError && <div className="text-[11px] text-red-500 mt-1">{advError}</div>}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Step form
// --------------------------------------------------------------------------
function StepForm({
  step,
  tools,
  approvalOptions,
  onChange,
}: {
  step: WorkflowStepStage;
  tools: WorkflowTool[];
  approvalOptions: string[];
  onChange: (patch: Partial<WorkflowStepStage>) => void;
}) {
  const tool = tools.find((t) => t.name === step.tool);
  const argEntries = Object.entries(step.args || {});
  // Approvals are auto-derived from preceding gates at runtime; the explicit
  // list here is only an override. Default the override UI open when one is set.
  const [showApprovalsOverride, setShowApprovalsOverride] = useState(
    (step.approvals || []).length > 0,
  );

  const setArgs = (entries: [string, SpecExpr][]) => {
    const obj: Record<string, SpecExpr> = {};
    entries.forEach(([k, v]) => { obj[k] = v; });
    onChange({ args: obj });
  };

  const toggleApproval = (gateType: string) => {
    const cur = new Set(step.approvals || []);
    if (cur.has(gateType)) cur.delete(gateType);
    else cur.add(gateType);
    onChange({ approvals: Array.from(cur) });
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <LabelWithHelp className={labelClass} help="The governed action this step performs. 'mutating' tools change real infrastructure/data and run through approval + audit; non-mutating tools only read.">
            Tool
          </LabelWithHelp>
          <select
            className={inputClass}
            value={step.tool}
            onChange={(e) => onChange({ tool: e.target.value })}
          >
            {tools.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
                {t.is_mutating ? ' \u2022 mutating' : ''}
              </option>
            ))}
          </select>
        </div>
        <div>
          <LabelWithHelp className={labelClass} help="Optional timeline/live-graph marker recorded when this step succeeds (e.g. access_granted). Set it only on a meaningful provisioning milestone — omit it on notification/closing steps, and don't set it equal to the workflow's complete_fact (that's written automatically on completion).">
            Success fact (optional)
          </LabelWithHelp>
          <input
            className={inputClass}
            value={step.success_fact || ''}
            placeholder="access_granted"
            onChange={(e) => onChange({ success_fact: e.target.value || null })}
          />
        </div>
      </div>

      {tool && (
        <div className="text-[11px] text-gray-500 -mt-1">
          {tool.description}{' '}
          <span className={`ml-1 px-1.5 py-0.5 rounded text-[10px] ${
            tool.is_mutating ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-500'
          }`}>{tool.side_effect_class}</span>
        </div>
      )}

      {approvalOptions.length > 0 && (
        <div>
          <LabelWithHelp className={labelClass} help="By default a step inherits the approvals of every gate before it — the graph guarantees those gates passed before this step runs, and that derived set is what policy enforcement sees. You rarely need to touch this; use the override only to change the set a step attests (e.g. apply after a specific platform_admin review).">
            Prior approvals
          </LabelWithHelp>
          <div className="text-[11px] text-gray-500 flex flex-wrap items-center gap-1">
            <span>Auto-applied from earlier gates:</span>
            {approvalOptions.map((g) => (
              <code key={g} className="px-1 py-0.5 rounded bg-gray-100 text-gray-600">{g}</code>
            ))}
            <button
              type="button"
              className="ml-1 text-accent hover:underline"
              onClick={() => setShowApprovalsOverride((v) => !v)}
            >
              {showApprovalsOverride ? 'Hide override' : 'Override'}
            </button>
          </div>
          {showApprovalsOverride && (
            <div className="mt-1.5">
              <div className="flex flex-wrap gap-2">
                {approvalOptions.map((g) => (
                  <label key={g} className="inline-flex items-center gap-1.5 text-xs border border-gray-200 rounded-md px-2 py-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={(step.approvals || []).includes(g)}
                      onChange={() => toggleApproval(g)}
                    />
                    {g}
                  </label>
                ))}
              </div>
              <div className="text-[11px] text-gray-400 mt-1">
                Leave all unchecked to use the auto-derived set.
              </div>
            </div>
          )}
        </div>
      )}

      <RunIfEditor
        value={step.run_if}
        onChange={(runIf) => onChange({ run_if: runIf })}
      />

      <div>
        <LabelWithHelp className={labelClass} help="The values passed to the tool. Each argument can be pulled 'From request field', set as 'Fixed text', the 'Entire request', a 'List of fields', or an 'Advanced' raw expression. Field values are filled in from the actual request at run time.">
          Tool arguments
        </LabelWithHelp>
        {tool && (tool.args?.length ?? 0) > 0 && (() => {
          const accepted = tool.args ?? [];
          const required = tool.required_args ?? [];
          const setKeys = new Set(argEntries.map(([k]) => k));
          const unknown = argEntries.map(([k]) => k).filter((k) => !accepted.includes(k));
          return (
            <div className="text-[11px] text-gray-500 mb-1.5 space-y-1">
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-gray-400">Accepts:</span>
                {accepted.map((a) => {
                  const isSet = setKeys.has(a);
                  const isReq = required.includes(a);
                  return (
                    <button
                      key={a}
                      type="button"
                      disabled={isSet}
                      onClick={() => setArgs([...argEntries, [a, { $var: '' }]])}
                      title={isSet ? 'Already set' : `Add ${a}`}
                      className={`px-1.5 py-0.5 rounded border text-[10px] ${
                        isSet
                          ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-default'
                          : 'bg-white text-blue-700 border-blue-200 hover:bg-blue-50'
                      }`}
                    >
                      {a}{isReq ? '*' : ''}
                    </button>
                  );
                })}
                {required.length > 0 && <span className="text-gray-400">(* required)</span>}
              </div>
              {unknown.length > 0 && (
                <div className="text-amber-700">
                  Not accepted by this tool (dropped at runtime): {unknown.join(', ')}
                </div>
              )}
            </div>
          );
        })()}
        <div className="space-y-2">
          {argEntries.map(([key, val], i) => (
            <ArgRow
              key={i}
              name={key}
              value={val}
              onRename={(newName) => {
                const next = argEntries.map((e, idx): [string, SpecExpr] => (idx === i ? [newName, e[1]] : e));
                setArgs(next);
              }}
              onChangeValue={(expr) => {
                const next = argEntries.map((e, idx): [string, SpecExpr] => (idx === i ? [e[0], expr] : e));
                setArgs(next);
              }}
              onRemove={() => setArgs(argEntries.filter((_, idx) => idx !== i))}
            />
          ))}
          <Button type="button" variant="ghost" size="sm"
            onClick={() => setArgs([...argEntries, [`arg_${argEntries.length + 1}`, { $var: '' }]])}>
            <Plus className="w-3.5 h-3.5 mr-1" /> Add argument
          </Button>
        </div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------
// Argument row (friendly expression editor)
// --------------------------------------------------------------------------
const ARG_KINDS: { value: ArgKind; label: string }[] = [
  { value: 'field', label: 'From request field' },
  { value: 'text', label: 'Fixed text' },
  { value: 'context', label: 'Entire request' },
  { value: 'list', label: 'List of fields' },
  { value: 'advanced', label: 'Advanced (JSON)' },
];

function ArgRow({
  name,
  value,
  onRename,
  onChangeValue,
  onRemove,
}: {
  name: string;
  value: SpecExpr;
  onRename: (n: string) => void;
  onChangeValue: (expr: SpecExpr) => void;
  onRemove: () => void;
}) {
  const av = exprToArgValue(value);
  const [advDraft, setAdvDraft] = useState<string | null>(null);
  const [advError, setAdvError] = useState<string | null>(null);

  const emit = (next: ArgValue) => {
    if (next.kind !== 'advanced') {
      onChangeValue(argValueToExpr(next));
      return;
    }
    setAdvDraft(next.raw ?? '');
    try {
      onChangeValue(JSON.parse(next.raw || 'null'));
      setAdvError(null);
    } catch {
      // keep the user's draft; surface the error and don't corrupt the spec
      setAdvError('Invalid JSON');
    }
  };

  const changeKind = (kind: ArgKind) => {
    setAdvDraft(null);
    setAdvError(null);
    if (kind === 'field') emit({ kind, field: av.field || '', hasDefault: false });
    else if (kind === 'text') emit({ kind, text: av.text || '' });
    else if (kind === 'context') emit({ kind });
    else if (kind === 'list') emit({ kind, items: av.items || [] });
    else emit({ kind, raw: av.raw || JSON.stringify(value, null, 2) });
  };

  return (
    <div className="flex items-start gap-2">
      <input
        className={`${inputBase} w-40 shrink-0`}
        value={name}
        placeholder="param"
        onChange={(e) => onRename(e.target.value)}
      />
      <select className={`${inputBase} w-44 shrink-0`} value={av.kind}
        onChange={(e) => changeKind(e.target.value as ArgKind)}>
        {ARG_KINDS.map((k) => (
          <option key={k.value} value={k.value}>{k.label}</option>
        ))}
      </select>
      <div className="flex-1">
        {av.kind === 'field' && (
          <div className="flex items-center gap-2">
            <input className={inputClass} placeholder="field name" value={av.field || ''}
              onChange={(e) => emit({ ...av, field: e.target.value })} />
            <label className="inline-flex items-center gap-1 text-[11px] text-gray-500 whitespace-nowrap">
              <input type="checkbox" checked={!!av.hasDefault}
                onChange={(e) => emit({ ...av, hasDefault: e.target.checked })} />
              default
            </label>
            {av.hasDefault && (
              <input className={`${inputBase} w-28 shrink-0`} placeholder="default" value={av.default || ''}
                onChange={(e) => emit({ ...av, default: e.target.value })} />
            )}
          </div>
        )}
        {av.kind === 'text' && (
          <input className={inputClass} placeholder="fixed value" value={av.text || ''}
            onChange={(e) => emit({ ...av, text: e.target.value })} />
        )}
        {av.kind === 'context' && (
          <div className="text-xs text-gray-500 h-9 flex items-center">Passes the entire request payload.</div>
        )}
        {av.kind === 'list' && (
          <input className={inputClass} placeholder="field1, field2"
            value={(av.items || []).join(', ')}
            onChange={(e) => emit({ ...av, items: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
        )}
        {av.kind === 'advanced' && (
          <div>
            <textarea
              className="w-full border border-gray-300 rounded-md p-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-accent"
              rows={3} value={advDraft ?? av.raw ?? ''}
              onChange={(e) => emit({ ...av, raw: e.target.value })} />
            {advError && <div className="text-[11px] text-red-500 mt-0.5">{advError}</div>}
          </div>
        )}
      </div>
      <button type="button" className="text-gray-400 hover:text-red-500 mt-2" onClick={onRemove}>
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

export default WorkflowEditor;
