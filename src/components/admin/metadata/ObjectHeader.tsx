import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import type { ObjectDetail } from '../../../types/metadata';
import { AttentionBadge, EnvBadge } from './badges';
import { TYPE_LABELS } from './format';
import { DescriptionField, TagsField } from './InlineEdit';
import { browsePath } from './paths';
import { hasEdits, seedFromDetail, useStagingStore } from './staging';

/** All catalogs › catalog › schema › table, each a link except the last. */
export function Breadcrumb({ catalog, schema, table }: { catalog?: string; schema?: string; table?: string }) {
  const parts: { label: string; to?: string }[] = [{ label: 'All catalogs', to: catalog ? browsePath() : undefined }];
  if (catalog) parts.push({ label: catalog, to: schema ? browsePath(catalog) : undefined });
  if (schema) parts.push({ label: schema, to: table ? browsePath(catalog, schema) : undefined });
  if (table) parts.push({ label: table });
  return (
    <nav aria-label="Where you are" className="flex flex-wrap items-center gap-1 text-sm text-gray-500">
      {parts.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />}
          {p.to ? (
            <Link to={p.to} className="hover:text-gray-900 hover:underline">
              {p.label}
            </Link>
          ) : (
            <span className="font-medium text-gray-900" aria-current="page">
              {p.label}
            </span>
          )}
        </Fragment>
      ))}
    </nav>
  );
}

/** Name, description and tags of one object, editable in place. */
export function ObjectHeader({ detail, title, actions }: { detail: ObjectDetail; title: string; actions?: ReactNode }) {
  const seed = seedFromDetail(detail);
  const unsaved = useStagingStore((s) => !!s.targets[seed.key] && hasEdits(s.targets[seed.key]));
  const desired = detail.desired?.status === 'active' ? detail.desired : null;
  const summary = {
    managed: !!desired,
    object_state: desired?.object_state ?? null,
    drift: detail.drift.some((d) => !d.exempt),
    last_apply_status: desired?.last_apply_status ?? null,
  };

  return (
    <header className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-start gap-2">
        <h3 className="break-all font-mono text-lg font-semibold text-gray-900">{title}</h3>
        <span className="mt-1 text-xs text-gray-500">{TYPE_LABELS[seed.securableType]}</span>
        <span className="mt-0.5">
          <EnvBadge env={seed.environment} />
        </span>
        <span className="mt-0.5">
          <AttentionBadge summary={summary} unsaved={unsaved} />
        </span>
        {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>
      <DescriptionField seed={seed} />
      <TagsField seed={seed} />
    </header>
  );
}
