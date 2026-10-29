import { Bot, Cog, User } from 'lucide-react';
import { Badge, type Tone } from '../../rbac/ui';
import type { ActorType, ApplyStatus, ObjectState, ObjectSummary, PlanStatus, RunStatus } from '../../../types/metadata';
import { useMetadata } from './context';
import { ACTOR_TYPE_LABELS, reportOnlyNote, sourceLabel, syncsObject, type Outcome } from './format';

const ACTOR_ICONS = { user: User, service_principal: Bot, system: Cog };

export function ActorTypeBadge({ type }: { type: ActorType | null | undefined }) {
  if (!type) return null;
  const Icon = ACTOR_ICONS[type];
  return (
    <Badge tone={type === 'user' ? 'gray' : type === 'service_principal' ? 'indigo' : 'blue'}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {ACTOR_TYPE_LABELS[type]}
    </Badge>
  );
}

export function SourceBadge({ source }: { source: string | null | undefined }) {
  if (!source) return null;
  return <Badge tone={source === 'revert' ? 'amber' : 'gray'}>{sourceLabel(source)}</Badge>;
}

export function EnvBadge({ env }: { env: string | null | undefined }) {
  const { config } = useMetadata();
  if (!env) return null;
  const index = config.environments.findIndex((e) => e.key === env);
  const strict = config.environments[index]?.approval === 'all';
  const tone: Tone = strict ? 'red' : index === 0 ? 'green' : 'blue';
  return (
    <Badge tone={tone} title={strict ? `Environment: ${env} (every edit needs approval)` : `Environment: ${env}`}>
      {env}
    </Badge>
  );
}

const STATE: Record<ObjectState, { tone: Tone; label: string; title: string }> = {
  exists: { tone: 'green', label: 'Exists', title: 'The object exists in Unity Catalog.' },
  pending: { tone: 'blue', label: 'Not created yet', title: 'Tags were prepared before this existed; they’re applied when it’s created in Unity Catalog.' },
  orphaned: { tone: 'amber', label: 'Missing', title: 'The object was dropped from Unity Catalog; its metadata is kept in case it comes back.' },
};

const APPLY: Record<ApplyStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'blue', label: 'Waiting to sync' },
  applied: { tone: 'green', label: 'In Unity Catalog' },
  failed: { tone: 'red', label: 'Couldn’t update Unity Catalog' },
  skipped: { tone: 'gray', label: 'Skipped' },
};

export function ApplyBadge({ status, fqnKey }: { status: ApplyStatus | null | undefined; fqnKey: string }) {
  const enforce = syncsObject(useMetadata().config, fqnKey);
  if (!status) return null;
  if (status === 'pending' && !enforce) {
    return <Badge tone="blue">Saved here only</Badge>;
  }
  return <Badge tone={APPLY[status].tone}>{APPLY[status].label}</Badge>;
}

export function DriftBadge({ title }: { title?: string }) {
  return (
    <Badge tone="amber" title={title ?? 'Unity Catalog has different tags or a different description than the ones saved here.'}>
      Out of sync
    </Badge>
  );
}

/** At most one plain-language status, shown only when something needs attention. */
export function AttentionBadge({ summary: s, unsaved }: {
  summary: Pick<ObjectSummary, 'fqn_key' | 'managed' | 'object_state' | 'drift' | 'last_apply_status'>;
  unsaved?: boolean;
}) {
  const { config } = useMetadata();
  const enforce = syncsObject(config, s.fqn_key);
  if (unsaved) return <Badge tone="indigo" title="You changed this here but haven’t saved it yet.">Unsaved changes</Badge>;
  if (!s.managed) return null;
  if (s.last_apply_status === 'failed') {
    return <Badge tone="red" title="The last attempt to write this to Unity Catalog failed. Open it to see why.">Couldn’t update Unity Catalog</Badge>;
  }
  if (s.object_state === 'pending') return <Badge tone="blue" title={STATE.pending.title}>Not created yet</Badge>;
  if (s.object_state === 'orphaned') return <Badge tone="amber" title={STATE.orphaned.title}>Missing from Unity Catalog</Badge>;
  if (s.last_apply_status === 'pending') {
    return (
      <Badge
        tone="blue"
        title={
          enforce ? 'Saved here; Unity Catalog is updated in the background.' : `Saved here. Unity Catalog isn’t updated: ${reportOnlyNote(config)}`
        }
      >
        {enforce ? 'Waiting to sync' : 'Saved here only'}
      </Badge>
    );
  }
  if (s.drift) return <DriftBadge />;
  return null;
}

const RUN: Record<RunStatus, { tone: Tone; label: string }> = {
  running: { tone: 'blue', label: 'Running' },
  completed: { tone: 'green', label: 'Completed' },
  partial: { tone: 'amber', label: 'Partly applied' },
  failed: { tone: 'red', label: 'Failed' },
  paused: { tone: 'red', label: 'Paused' },
  skipped: { tone: 'gray', label: 'Skipped' },
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={RUN[status].tone}>{RUN[status].label}</Badge>;
}

const PLAN: Record<PlanStatus, { tone: Tone; label: string }> = {
  planned: { tone: 'blue', label: 'Planned' },
  applied: { tone: 'green', label: 'Applied' },
  noop: { tone: 'gray', label: 'No change' },
  failed: { tone: 'red', label: 'Failed' },
  skipped: { tone: 'gray', label: 'Skipped' },
  reported: { tone: 'gray', label: 'Report only' },
};

export function PlanStatusBadge({ status }: { status: PlanStatus }) {
  return <Badge tone={PLAN[status].tone}>{PLAN[status].label}</Badge>;
}

const OUTCOME: Record<Outcome, { tone: Tone; label: string }> = {
  direct: { tone: 'green', label: 'Saved directly' },
  approval: { tone: 'amber', label: 'Needs approval' },
  denied: { tone: 'red', label: 'Not allowed' },
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <Badge tone={OUTCOME[outcome].tone}>{OUTCOME[outcome].label}</Badge>;
}
