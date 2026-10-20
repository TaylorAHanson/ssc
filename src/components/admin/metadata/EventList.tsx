import { ArrowRight } from 'lucide-react';
import type { ActorType, Authorship, ChangeEvent } from '../../../types/metadata';
import { cn } from '../../../lib/utils';
import { ActorTypeBadge, SourceBadge } from './badges';
import { useMetadata } from './context';
import { actionLabel, eventValue, formatTime, relativeTime } from './format';

function Value({ snapshot }: { snapshot: Record<string, unknown> | null | undefined }) {
  const v = eventValue(snapshot);
  return (
    <span
      className={cn(
        'break-all rounded px-1 font-mono text-xs',
        v.kind === 'none' ? 'text-gray-400' : v.kind === 'keyonly' ? 'bg-gray-100 text-gray-600' : 'bg-gray-100 text-gray-800',
      )}
    >
      {v.text}
    </span>
  );
}

/** One before → after change, optionally with who made it and a link to its change set. */
export function EventRow({ event, showObject, showActor = true, showChangeSet = true }: {
  event: ChangeEvent;
  showObject?: boolean;
  showActor?: boolean;
  showChangeSet?: boolean;
}) {
  const { openChangeSet, openObject } = useMetadata();
  const [name, column] = event.fqn_key.split('::');
  return (
    <li className="space-y-1 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-gray-900">{actionLabel(event.action)}</span>
        {event.key && <span className="font-mono text-xs text-gray-700">{event.key}</span>}
        {showObject && (
          <button type="button" onClick={() => openObject(name, column ?? null)} className="font-mono text-xs text-primary hover:underline">
            {event.name ?? event.fqn_key.replace('::', '.')}
          </button>
        )}
        <span className="text-xs text-gray-500" title={formatTime(event.created_at)}>
          {relativeTime(event.created_at)}
        </span>
      </div>
      {(event.before || event.after) && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Value snapshot={event.before} />
          <ArrowRight className="h-3 w-3 text-gray-400" aria-label="changed to" />
          <Value snapshot={event.after} />
        </div>
      )}
      {(showActor || showChangeSet) && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
          {showActor && event.actor && <span>{event.actor}</span>}
          {showActor && <ActorTypeBadge type={event.actor_type} />}
          {showActor && <SourceBadge source={event.source} />}
          {showChangeSet && (
            <button type="button" onClick={() => openChangeSet(event.change_set_id)} className="text-primary hover:underline">
              View change
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function EventList({ events, showObject, showActor, showChangeSet, empty = 'No changes yet.' }: {
  events: ChangeEvent[];
  showObject?: boolean;
  showActor?: boolean;
  showChangeSet?: boolean;
  empty?: string;
}) {
  if (!events.length) return <p className="text-sm text-gray-500">{empty}</p>;
  return (
    <ul className="divide-y divide-gray-100">
      {events.map((e) => (
        <EventRow key={e.id} event={e} showObject={showObject} showActor={showActor} showChangeSet={showChangeSet} />
      ))}
    </ul>
  );
}

/** "Set by sam@… (Person) via Metadata Manager, 3 days ago". */
export function AuthorLine({ author }: { author: Authorship }) {
  const { openChangeSet } = useMetadata();
  if (!author.set_by && !author.set_at) return null;
  return (
    <span className="flex flex-wrap items-center gap-1 text-xs text-gray-500">
      {author.set_by && <span>{author.set_by}</span>}
      <ActorTypeBadge type={author.set_by_type as ActorType | null | undefined} />
      <SourceBadge source={author.source} />
      {author.set_at && <span title={formatTime(author.set_at)}>{relativeTime(author.set_at)}</span>}
      {author.change_set_id && (
        <button type="button" onClick={() => openChangeSet(author.change_set_id as string)} className="text-primary hover:underline">
          change
        </button>
      )}
    </span>
  );
}
