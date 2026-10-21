import { create } from 'zustand';
import type {
  ApprovalRouting,
  ChangeOp,
  ChangeRequest,
  ObjectDetail,
  ObjectSummary,
  SecurableType,
  Tags,
  VersionConflictItem,
} from '../../../types/metadata';
import { fqnKey } from './format';

/** One object with edits staged against the state it had when it was staged. */
export interface StagedTarget {
  key: string;
  name: string;
  column: string | null;
  securableType: SecurableType;
  environment: string | null;
  /** Declared by hand; may not exist in Unity Catalog yet. */
  declared: boolean;
  managed: boolean;
  baseTags: Tags;
  /** Tag key -> version; null when unknown (no expected_versions are sent). */
  baseVersions: Record<string, number> | null;
  baseComment: string | null;
  baseCommentVersion: number | null;
  set: Tags;
  unset: string[];
  /** Absent = unchanged; `{value: null}` clears the description. */
  comment?: { value: string | null };
}

export type TargetSeed = Omit<StagedTarget, 'set' | 'unset' | 'comment'>;

export function seedFromSummary(s: ObjectSummary): TargetSeed {
  return {
    key: s.fqn_key,
    name: s.name,
    column: s.column ?? null,
    securableType: s.securable_type,
    environment: s.environment ?? null,
    declared: false,
    managed: s.managed,
    baseTags: (s.managed ? s.tags : s.observed_tags ?? s.tags) ?? {},
    baseVersions: s.tag_versions ?? (s.managed ? null : {}),
    baseComment: (s.managed ? s.comment : s.observed_comment ?? s.comment) ?? null,
    baseCommentVersion: s.comment_version ?? (s.managed ? null : 0),
  };
}

export function seedFromDetail(d: ObjectDetail): TargetSeed {
  const desired = d.desired?.status === 'active' ? d.desired : null;
  const name = [d.key.catalog, d.key.schema_name, d.key.object_name].filter(Boolean).join('.');
  return {
    key: d.fqn_key,
    name,
    column: d.key.column_name ?? null,
    securableType: desired?.securable_type ?? d.observed?.securable_type ?? 'table',
    environment: desired?.environment ?? null,
    declared: false,
    managed: !!desired,
    baseTags: desired ? desired.tags : d.observed?.tags ?? {},
    baseVersions: desired ? Object.fromEntries(desired.tag_details.map((t) => [t.key, t.version])) : {},
    baseComment: desired ? desired.comment.value : d.observed?.comment ?? null,
    baseCommentVersion: desired ? desired.comment.version : 0,
  };
}

export function seedForNew(name: string, column: string | null, securableType: SecurableType, environment: string | null): TargetSeed {
  return {
    key: fqnKey(name, column),
    name: name.trim(),
    column: column?.trim() || null,
    securableType,
    environment,
    declared: true,
    managed: false,
    baseTags: {},
    baseVersions: {},
    baseComment: null,
    baseCommentVersion: 0,
  };
}

export function effectiveTags(t: StagedTarget): Tags {
  const out: Tags = { ...t.baseTags };
  t.unset.forEach((k) => delete out[k]);
  return { ...out, ...t.set };
}

export const effectiveComment = (t: StagedTarget): string | null => (t.comment ? t.comment.value : t.baseComment);

/** A declared object counts once it has a tag or a description; there's nothing to save before that. */
export const hasEdits = (t: StagedTarget): boolean =>
  Object.keys(t.set).length > 0 || t.unset.length > 0 || (t.comment !== undefined && (!t.declared || t.comment.value !== null));

export function editCount(t: StagedTarget): number {
  return Object.keys(t.set).length + t.unset.length + (t.comment ? 1 : 0);
}

function expected(t: StagedTarget, keys: string[]): Record<string, number> | undefined {
  if (!t.baseVersions) return undefined;
  const out: Record<string, number> = {};
  keys.forEach((k) => {
    if (t.baseVersions && k in t.baseVersions) out[k] = t.baseVersions[k];
    else if (!(k in t.baseTags)) out[k] = 0;
  });
  return Object.keys(out).length ? out : undefined;
}

export function opsFor(t: StagedTarget): ChangeOp[] {
  const base: Pick<ChangeOp, 'name' | 'column' | 'securable_type'> = {
    name: t.name,
    column: t.column,
    securable_type: t.declared || !t.managed ? t.securableType : null,
  };
  const ops: ChangeOp[] = [];
  if (!t.managed) {
    // The first desired row is enforced in full, so it must carry the tags the object already has.
    const tags = effectiveTags(t);
    const comment = effectiveComment(t);
    const zeros = Object.fromEntries(Object.keys(tags).map((k) => [k, 0]));
    ops.push({ ...base, op: 'set_tags', tags, expected_versions: zeros });
    if (comment !== null) ops.push({ ...base, op: 'set_comment', comment, expected_comment_version: 0 });
    return ops;
  }
  const setKeys = Object.keys(t.set);
  if (setKeys.length) ops.push({ ...base, op: 'set_tags', tags: { ...t.set }, expected_versions: expected(t, setKeys) });
  if (t.unset.length) ops.push({ ...base, op: 'unset_tags', keys: [...t.unset], expected_versions: expected(t, t.unset) });
  if (t.comment) {
    const version = t.baseCommentVersion ?? undefined;
    ops.push(
      t.comment.value === null
        ? { ...base, op: 'clear_comment', expected_comment_version: version }
        : { ...base, op: 'set_comment', comment: t.comment.value, expected_comment_version: version },
    );
  }
  return ops;
}

// --- Store ---------------------------------------------------------------------------

interface StagingState {
  targets: Record<string, StagedTarget>;
  order: string[];
  environments: string[];
  routing: ApprovalRouting;
  reason: string;
  setTag: (keys: string[], tagKey: string, value: string | null, seeds?: TargetSeed[]) => void;
  unsetTag: (keys: string[], tagKey: string, seeds?: TargetSeed[]) => void;
  setComment: (keys: string[], value: string | null, seeds?: TargetSeed[]) => void;
  /** Drops the staged edit for one tag (or the description when tagKey is null). */
  undoField: (key: string, tagKey: string | null) => void;
  remove: (key: string) => void;
  clear: () => void;
  /** After a version conflict: take the current values as the new base, keeping the edits. */
  rebase: (conflicts: VersionConflictItem[]) => void;
  replaceBase: (seed: TargetSeed) => void;
  setEnvironments: (envs: string[]) => void;
  setRouting: (routing: ApprovalRouting) => void;
  setReason: (reason: string) => void;
}

const withTargets = (state: StagingState, seeds: TargetSeed[] = []) => {
  const targets = { ...state.targets };
  const order = [...state.order];
  seeds.forEach((s) => {
    if (!targets[s.key]) {
      targets[s.key] = { ...s, set: {}, unset: [] };
      order.push(s.key);
    }
  });
  return { targets, order };
};

function editTarget(t: StagedTarget, fn: (t: StagedTarget) => StagedTarget): StagedTarget {
  return fn({ ...t, set: { ...t.set }, unset: [...t.unset] });
}

export const useStagingStore = create<StagingState>((set) => ({
  targets: {},
  order: [],
  environments: [],
  routing: 'request',
  reason: '',
  setTag: (keys, tagKey, value, seeds) =>
    set((state) => {
      const { targets, order } = withTargets(state, seeds);
      keys.forEach((k) => {
        const t = targets[k];
        if (!t) return;
        targets[k] = editTarget(t, (n) => {
          n.unset = n.unset.filter((u) => u !== tagKey);
          if (tagKey in n.baseTags && n.baseTags[tagKey] === value) delete n.set[tagKey];
          else n.set[tagKey] = value;
          return n;
        });
      });
      return { targets, order };
    }),
  unsetTag: (keys, tagKey, seeds) =>
    set((state) => {
      const { targets, order } = withTargets(state, seeds);
      keys.forEach((k) => {
        const t = targets[k];
        if (!t) return;
        targets[k] = editTarget(t, (n) => {
          delete n.set[tagKey];
          if (tagKey in n.baseTags && !n.unset.includes(tagKey)) n.unset.push(tagKey);
          return n;
        });
      });
      return { targets, order };
    }),
  setComment: (keys, value, seeds) =>
    set((state) => {
      const { targets, order } = withTargets(state, seeds);
      const normalized = value?.trim() ? value : null;
      keys.forEach((k) => {
        const t = targets[k];
        if (!t) return;
        targets[k] = { ...t, comment: normalized === t.baseComment ? undefined : { value: normalized } };
      });
      return { targets, order };
    }),
  undoField: (key, tagKey) =>
    set((state) => {
      const t = state.targets[key];
      if (!t) return {};
      const next = editTarget(t, (n) => {
        if (tagKey === null) n.comment = undefined;
        else {
          delete n.set[tagKey];
          n.unset = n.unset.filter((u) => u !== tagKey);
        }
        return n;
      });
      return { targets: { ...state.targets, [key]: next } };
    }),
  remove: (key) =>
    set((state) => {
      const targets = { ...state.targets };
      delete targets[key];
      return { targets, order: state.order.filter((k) => k !== key) };
    }),
  clear: () => set({ targets: {}, order: [], reason: '' }),
  rebase: (conflicts) =>
    set((state) => {
      const targets = { ...state.targets };
      conflicts.forEach((c) => {
        const t = targets[c.fqn_key];
        if (!t) return;
        if (c.key) {
          const baseTags = { ...t.baseTags };
          if (c.current_version === 0) delete baseTags[c.key];
          else baseTags[c.key] = c.current_value ?? null;
          targets[c.fqn_key] = { ...t, baseTags, baseVersions: { ...(t.baseVersions ?? {}), [c.key]: c.current_version } };
        } else {
          targets[c.fqn_key] = { ...t, baseComment: c.current_value ?? null, baseCommentVersion: c.current_version };
        }
      });
      return { targets };
    }),
  replaceBase: (seed) =>
    set((state) => {
      const t = state.targets[seed.key];
      if (!t) return {};
      return { targets: { ...state.targets, [seed.key]: { ...t, ...seed, declared: t.declared && !seed.managed } } };
    }),
  setEnvironments: (environments) => set({ environments }),
  setRouting: (routing) => set({ routing }),
  setReason: (reason) => set({ reason }),
}));

export function stagedList(state: Pick<StagingState, 'targets' | 'order'>): StagedTarget[] {
  return state.order.map((k) => state.targets[k]).filter((t): t is StagedTarget => !!t);
}

export function buildChangeRequest(state: Pick<StagingState, 'targets' | 'order' | 'environments' | 'routing' | 'reason'>): ChangeRequest {
  return {
    ops: stagedList(state).filter(hasEdits).flatMap(opsFor),
    reason: state.reason.trim(),
    environments: state.environments,
    on_approval_needed: state.routing,
  };
}
