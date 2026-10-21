import { Link, useLocation } from 'react-router-dom';
import { cn } from '../../../lib/utils';
import { METADATA_BASE, datasetsPath } from './paths';

/** Browse by catalog (catalog › schema › table) or by dataset (tables sharing a dataset tag). */
export function BrowseSwitch() {
  const onDatasets = useLocation().pathname.startsWith(datasetsPath);
  const options: { label: string; to: string; active: boolean }[] = [
    { label: 'Catalogs', to: METADATA_BASE, active: !onDatasets },
    { label: 'Datasets', to: datasetsPath, active: onDatasets },
  ];
  return (
    <nav aria-label="Browse by" className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-sm">
      {options.map((o) => (
        <Link
          key={o.label}
          to={o.to}
          aria-current={o.active ? 'page' : undefined}
          className={cn('rounded px-3 py-1 font-medium', o.active ? 'bg-primary/10 text-primary' : 'text-gray-600 hover:text-gray-900')}
        >
          {o.label}
        </Link>
      ))}
    </nav>
  );
}
