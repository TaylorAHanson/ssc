import { Bot, Cog, User } from 'lucide-react';
import { Badge, type Tone } from '../../rbac/ui';
import type { ActorType, ApplyStatus, ObjectState, PlanStatus, RunStatus } from '../../../types/metadata';
import { useMetadata } from './context';
import { ACTOR_TYPE_LABELS, sourceLabel, type Outcome } from './format';

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
  pending: { tone: 'blue', label: 'Not created yet', title: 'Declared ahead of time; its metadata is applied when the object is created.' },
  orphaned: { tone: 'amber', label: 'Missing', title: 'The object was dropped from Unity Catalog; its metadata is kept in case it comes back.' },
};

export function StateBadge({ state, managed }: { state: ObjectState | null | undefined; managed: boolean }) {
  if (!managed) {
    return (
      <Badge tone="gray" title="Unity Catalog has this object but nobody manages its metadata here yet.">
        Not managed
      </Badge>
    );
  }
  if (!state || state === 'exists') return null;
  const s = STATE[state];
  return (
    <Badge tone={s.tone} title={s.title}>
      {s.label}
    </Badge>
  );
}

const APPLY: Record<ApplyStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'blue', label: 'Waiting to apply' },
  applied: { tone: 'green', label: 'Applied' },
  failed: { tone: 'red', label: 'Apply failed' },
  skipped: { tone: 'gray', label: 'Skipped' },
};

export function ApplyBadge({ status }: { status: ApplyStatus | null | undefined }) {
  if (!status) return null;
  return <Badge tone={APPLY[status].tone}>{APPLY[status].label}</Badge>;
}

export function DriftBadge({ title }: { title?: string }) {
  return (
    <Badge tone="amber" title={title ?? 'Unity Catalog doesn’t match what’s declared here.'}>
      Drift
    </Badge>
  );
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
