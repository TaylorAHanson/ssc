/**
 * Helpers for the visual workflow editor: convert between the backend's safe
 * JSON expression language (app/v2/expr.py) and friendly UI models, and lay a
 * spec out as a graph for preview.
 *
 * The editor exposes common patterns (a field reference, a literal, the whole
 * request, a list of fields) with a raw-JSON escape hatch for anything the
 * friendly model can't represent — so authors never lose access to the full
 * expression language.
 */
import type {
  GateType,
  SpecExpr,
  WorkflowGraphSpec,
  WorkflowStage,
} from '../services/api';

// --- expression detection -------------------------------------------------

function isOperation(node: unknown): node is Record<string, unknown> {
  if (typeof node !== 'object' || node === null || Array.isArray(node)) return false;
  const keys = Object.keys(node as object);
  return keys.length === 1 && keys[0].startsWith('$');
}

function opName(node: Record<string, unknown>): string {
  return Object.keys(node)[0];
}

// --- arg value model ------------------------------------------------------

export type ArgKind = 'field' | 'text' | 'context' | 'list' | 'advanced';

export interface ArgValue {
  kind: ArgKind;
  field?: string;        // for 'field'
  hasDefault?: boolean;  // for 'field'
  default?: string;      // for 'field' (raw text, JSON-parsed on save)
  text?: string;         // for 'text' literal
  items?: string[];      // for 'list' (each item is a field name)
  raw?: string;          // for 'advanced' (raw JSON)
}

function coerce(text: string): unknown {
  const t = (text ?? '').trim();
  if (t === '') return '';
  try {
    return JSON.parse(t);
  } catch {
    return text;
  }
}

export function exprToArgValue(expr: SpecExpr): ArgValue {
  if (isOperation(expr as Record<string, unknown>)) {
    const node = expr as Record<string, unknown>;
    const op = opName(node);
    if (op === '$ctx') return { kind: 'context' };
    if (op === '$var') {
      const v = node.$var;
      if (typeof v === 'string') return { kind: 'field', field: v, hasDefault: false };
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        const { path, default: def } = v as { path?: unknown; default?: unknown };
        if (typeof path === 'string' && !isOperation(def)) {
          return {
            kind: 'field',
            field: path,
            hasDefault: true,
            default: typeof def === 'string' ? def : JSON.stringify(def),
          };
        }
      }
    }
    if (op === '$list') {
      const arr = node.$list;
      if (Array.isArray(arr)) {
        const items: string[] = [];
        let ok = true;
        for (const el of arr) {
          if (isOperation(el) && opName(el as Record<string, unknown>) === '$var' &&
              typeof (el as Record<string, unknown>).$var === 'string') {
            items.push((el as Record<string, unknown>).$var as string);
          } else {
            ok = false;
            break;
          }
        }
        if (ok) return { kind: 'list', items };
      }
    }
    // Any other operation -> advanced.
    return { kind: 'advanced', raw: JSON.stringify(expr, null, 2) };
  }
  // Literal scalar -> text. Plain object/array -> advanced.
  if (typeof expr === 'string') return { kind: 'text', text: expr };
  if (typeof expr === 'number' || typeof expr === 'boolean') {
    return { kind: 'text', text: String(expr) };
  }
  return { kind: 'advanced', raw: JSON.stringify(expr ?? null, null, 2) };
}

export function argValueToExpr(v: ArgValue): SpecExpr {
  switch (v.kind) {
    case 'context':
      return { $ctx: true };
    case 'field':
      if (v.hasDefault) {
        return { $var: { path: v.field || '', default: coerce(v.default ?? '') } };
      }
      return { $var: v.field || '' };
    case 'list':
      return { $list: (v.items || []).map((f) => ({ $var: f })) };
    case 'text':
      return v.text ?? '';
    case 'advanced':
    default:
      return JSON.parse(v.raw || 'null');
  }
}

// --- auto-approve model ---------------------------------------------------

export type ConditionOp = 'truthy' | 'falsy' | 'equals';

export interface Condition {
  field: string;
  op: ConditionOp;
  value?: string; // for 'equals'
}

export type AutoApproveMode = 'always' | 'conditions' | 'advanced';

export interface AutoApproveModel {
  mode: AutoApproveMode;
  conditions: Condition[]; // OR'd together
  raw?: string;            // for 'advanced'
}

function exprToCondition(expr: unknown): Condition | null {
  if (!isOperation(expr)) return null;
  const node = expr as Record<string, unknown>;
  const op = opName(node);
  if (op === '$var' && typeof node.$var === 'string') {
    return { field: node.$var, op: 'truthy' };
  }
  if (op === '$bool' && isOperation(node.$bool) &&
      opName(node.$bool as Record<string, unknown>) === '$var' &&
      typeof (node.$bool as Record<string, unknown>).$var === 'string') {
    return { field: (node.$bool as Record<string, unknown>).$var as string, op: 'truthy' };
  }
  if (op === '$not' && isOperation(node.$not) &&
      opName(node.$not as Record<string, unknown>) === '$var' &&
      typeof (node.$not as Record<string, unknown>).$var === 'string') {
    return { field: (node.$not as Record<string, unknown>).$var as string, op: 'falsy' };
  }
  if (op === '$eq' && Array.isArray(node.$eq) && node.$eq.length === 2) {
    const [a, b] = node.$eq as unknown[];
    if (isOperation(a) && opName(a as Record<string, unknown>) === '$var' &&
        typeof (a as Record<string, unknown>).$var === 'string' && !isOperation(b)) {
      return {
        field: (a as Record<string, unknown>).$var as string,
        op: 'equals',
        value: typeof b === 'string' ? b : JSON.stringify(b),
      };
    }
  }
  return null;
}

export function autoApproveToModel(expr: SpecExpr | null | undefined): AutoApproveModel {
  if (expr === null || expr === undefined) {
    return { mode: 'always', conditions: [] };
  }
  // OR of simple conditions?
  if (isOperation(expr) && opName(expr as Record<string, unknown>) === '$or') {
    const arr = (expr as Record<string, unknown>).$or;
    if (Array.isArray(arr)) {
      const conds = arr.map(exprToCondition);
      if (conds.every((c): c is Condition => c !== null)) {
        return { mode: 'conditions', conditions: conds };
      }
    }
  }
  const single = exprToCondition(expr);
  if (single) return { mode: 'conditions', conditions: [single] };
  return { mode: 'advanced', conditions: [], raw: JSON.stringify(expr, null, 2) };
}

function conditionToExpr(c: Condition): SpecExpr {
  if (c.op === 'truthy') return { $bool: { $var: c.field } };
  if (c.op === 'falsy') return { $not: { $var: c.field } };
  return { $eq: [{ $var: c.field }, coerce(c.value ?? '')] };
}

export function modelToAutoApprove(m: AutoApproveModel): SpecExpr | null {
  if (m.mode === 'always') return null;
  if (m.mode === 'advanced') return JSON.parse(m.raw || 'null');
  const conds = m.conditions.filter((c) => c.field.trim());
  if (conds.length === 0) return null;
  if (conds.length === 1) return conditionToExpr(conds[0]);
  return { $or: conds.map(conditionToExpr) };
}

// --- graph layout for preview --------------------------------------------

export interface FlowNode {
  id: string;
  label: string;
  sublabel?: string;
  kind: 'start' | 'gate' | 'step' | 'complete' | 'rejected';
  x: number;
  y: number;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  tone?: 'normal' | 'reject';
}

export const GATE_TYPES: GateType[] = [
  'manager',
  'platform_admin',
  'data_owner',
  'training',
  'pr_merge',
  'children',
];

/** Lay a spec out top-to-bottom: pending -> stages -> complete, gates branch to rejected. */
export function specToFlow(spec: WorkflowGraphSpec): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const COL_X = 40;
  const REJECT_X = 320;
  const ROW = 96;
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];

  const stages = spec.stages || [];
  let row = 0;
  nodes.push({ id: 'pending', label: 'Submitted', kind: 'start', x: COL_X, y: row * ROW });
  let prev = 'pending';
  let anyGate = false;

  stages.forEach((s) => {
    row += 1;
    const id = s.name;
    if (s.kind === 'gate') {
      anyGate = true;
      nodes.push({
        id, label: s.name, sublabel: `${s.type} gate`, kind: 'gate', x: COL_X, y: row * ROW,
      });
      edges.push({ id: `${prev}->${id}`, source: prev, target: id });
      edges.push({ id: `${id}->rejected`, source: id, target: 'rejected', label: 'reject', tone: 'reject' });
    } else {
      const step = s as WorkflowStage & { tool?: string; run_if?: unknown };
      const conditional = step.run_if !== undefined && step.run_if !== null;
      nodes.push({
        id,
        label: s.name,
        sublabel: conditional ? `${step.tool ?? ''} (conditional)` : step.tool,
        kind: 'step',
        x: COL_X,
        y: row * ROW,
      });
      edges.push({ id: `${prev}->${id}`, source: prev, target: id, label: conditional ? 'if' : undefined });
    }
    prev = id;
  });

  row += 1;
  nodes.push({ id: 'complete', label: 'Completed', kind: 'complete', x: COL_X, y: row * ROW });
  edges.push({ id: `${prev}->complete`, source: prev, target: 'complete' });

  if (anyGate) {
    nodes.push({ id: 'rejected', label: 'Rejected', kind: 'rejected', x: REJECT_X, y: (row * ROW) / 2 });
  }
  return { nodes, edges };
}

// --- field discovery (for the dry-run sample input) -----------------------

function collectFromNode(node: unknown, out: Set<string>): void {
  if (Array.isArray(node)) {
    node.forEach((n) => collectFromNode(n, out));
    return;
  }
  if (!isOperation(node as Record<string, unknown>)) {
    if (node && typeof node === 'object') {
      Object.values(node as Record<string, unknown>).forEach((v) => collectFromNode(v, out));
    }
    return;
  }
  const op = opName(node as Record<string, unknown>);
  const arg = (node as Record<string, unknown>)[op];
  if (op === '$var') {
    let path: string | undefined;
    if (typeof arg === 'string') path = arg;
    else if (arg && typeof arg === 'object' && typeof (arg as { path?: unknown }).path === 'string') {
      path = (arg as { path: string }).path;
      collectFromNode((arg as { default?: unknown }).default, out);
    }
    if (path) out.add(path.split('.')[0]);
    return;
  }
  // $item is per-iteration, not a request field — skip its path but recurse defaults.
  collectFromNode(arg, out);
}

/** Every top-level request field the workflow reads (across args, auto-approve,
 *  for_each, item_args). Powers the dry-run sample-input scaffold. */
export function collectVarPaths(spec: WorkflowGraphSpec): string[] {
  const out = new Set<string>();
  for (const s of spec.stages || []) {
    if (s.kind === 'gate') {
      collectFromNode(s.auto_approve, out);
    } else {
      const step = s as { args?: Record<string, unknown>; for_each?: unknown; item_args?: Record<string, unknown>; run_if?: unknown };
      if (step.args) Object.values(step.args).forEach((v) => collectFromNode(v, out));
      collectFromNode(step.for_each, out);
      if (step.item_args) Object.values(step.item_args).forEach((v) => collectFromNode(v, out));
      collectFromNode(step.run_if, out);
    }
  }
  out.delete('');
  return Array.from(out).sort();
}

// --- new-stage factories --------------------------------------------------

export function newGate(index: number): WorkflowStage {
  return { kind: 'gate', name: `approval_${index}`, type: 'manager', waiting_status: 'manager_approval' };
}

export function newStep(index: number, defaultTool: string): WorkflowStage {
  return { kind: 'step', name: `step_${index}`, tool: defaultTool, approvals: [], args: {} };
}
