import { create } from 'zustand';
import { describeObject } from '../../../services/metadataApi';
import type { DescribeMode, DescribeSkip, MetadataConfig, ObjectDetail } from '../../../types/metadata';
import { plural } from './format';
import { seedFromDetail, seedFromSummary, useStagingStore, type StagedTarget, type TargetSeed } from './staging';

export const AI_NOTE = 'AI suggestions can be wrong. Review each one before saving.';

/** Target keys (fqn_key) waiting for a suggestion, so each field can show it's loading. */
export const useAiPending = create<{ pending: Record<string, true>; mark: (keys: string[], on: boolean) => void }>((set) => ({
  pending: {},
  mark: (keys, on) =>
    set((state) => {
      const pending = { ...state.pending };
      keys.forEach((k) => {
        if (on) pending[k] = true;
        else delete pending[k];
      });
      return { pending };
    }),
}));

export interface SuggestOptions {
  mode: DescribeMode;
  /** Omit for every column; [] for none. */
  columns?: string[];
  includeTable?: boolean;
  /** Replace descriptions the person typed but hasn't saved; off for bulk runs. */
  replaceTyped?: boolean;
}

export interface SuggestOutcome {
  staged: number;
  /** Suggestions not staged because the person had typed a description there. */
  keptTyped: number;
  skipped: DescribeSkip[];
}

const typed = (t: StagedTarget | undefined) => !!t?.comment && !t.comment.ai;

const globRegex = (pattern: string) => new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);

/** Whether this deployment accepts edits in `catalog` (blank list = every catalog), as the server checks. */
export function editsCatalog(config: MetadataConfig, catalog: string): boolean {
  const name = catalog.trim().toLowerCase();
  return !config.managed_catalogs.length || config.managed_catalogs.some((p) => globRegex(p.toLowerCase()).test(name));
}

/** Asks the model for suggestions and stages them as unsaved description edits. Throws on a failed call. */
export async function suggestDescriptions(detail: ObjectDetail, opts: SuggestOptions): Promise<SuggestOutcome> {
  const tableSeed = seedFromDetail(detail);
  const columnSeeds = new Map<string, TargetSeed>(
    detail.columns.filter((c) => c.column).map((c) => [(c.column as string).toLowerCase(), seedFromSummary(c)]),
  );
  const wanted = opts.columns ? opts.columns.map((c) => columnSeeds.get(c.toLowerCase())).filter((s): s is TargetSeed => !!s) : [...columnSeeds.values()];
  const keys = [...wanted.map((s) => s.key), ...(opts.includeTable ? [tableSeed.key] : [])];
  const { mark } = useAiPending.getState();
  mark(keys, true);
  try {
    const result = await describeObject({
      object: detail.fqn_key,
      columns: opts.columns,
      mode: opts.mode,
      include_table: !!opts.includeTable,
    });
    const targets = useStagingStore.getState().targets;
    const suggestions: { seed: TargetSeed; value: string }[] = [];
    let keptTyped = 0;
    const offer = (seed: TargetSeed | undefined, value: string | null | undefined) => {
      if (!seed || !value?.trim()) return;
      if (!opts.replaceTyped && typed(targets[seed.key])) keptTyped += 1;
      else suggestions.push({ seed, value });
    };
    offer(opts.includeTable ? tableSeed : undefined, result.table);
    Object.entries(result.columns).forEach(([name, value]) => offer(columnSeeds.get(name.toLowerCase()), value));
    useStagingStore.getState().stageSuggestions(suggestions);
    return { staged: suggestions.length, keptTyped, skipped: result.skipped };
  } finally {
    mark(keys, false);
  }
}

/** A short summary of what a run staged and left out, for a toast. */
export function outcomeMessage(o: SuggestOutcome): string {
  const failed = o.skipped.filter((s) => s.reason === 'model_error').length;
  const overLimit = o.skipped.filter((s) => s.reason === 'over_limit').length;
  const parts: string[] = [];
  if (o.staged) parts.push(`Added ${plural(o.staged, 'AI suggestion')} as unsaved changes. ${AI_NOTE}`);
  else parts.push('No new suggestions to add.');
  if (o.keptTyped) parts.push(`Kept what you typed for ${plural(o.keptTyped, 'description')}.`);
  if (failed) parts.push(`Couldn’t get suggestions for ${plural(failed, 'description')}; try again.`);
  if (overLimit) parts.push(`${plural(overLimit, 'column')} went over the limit for one request; save these, then generate again.`);
  return parts.join(' ');
}
