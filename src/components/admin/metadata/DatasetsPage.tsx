import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, X } from 'lucide-react';
import { listDatasets } from '../../../services/metadataApi';
import { Input } from '../../ui/input';
import { EmptyState, ErrorBanner, Skeleton } from '../../rbac/ui';
import { DriftBadge } from './badges';
import { BrowseSwitch } from './BrowseSwitch';
import { useMetadata } from './context';
import { errorMessage, plural } from './format';
import { datasetPath } from './paths';
import { useAsync } from './useAsync';

const SHOWN_CATALOGS = 3;

/** Every dataset: the tables and views that share a value of the dataset tag. */
export function DatasetsPage() {
  const { refreshToken } = useMetadata();
  const { data, error, loading, reload } = useAsync(listDatasets, { keepPrevious: true, token: refreshToken });
  const [text, setText] = useState('');
  const needle = text.trim().toLowerCase();
  const shown = (data ?? []).filter((d) => !needle || d.name.toLowerCase().includes(needle));

  return (
    <div className="space-y-4">
      <BrowseSwitch />
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Find a dataset" aria-label="Find a dataset" className="pl-9 pr-8" />
        {text && (
          <button type="button" onClick={() => setText('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700" aria-label="Clear the search">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load the datasets')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && shown.length === 0 ? (
        <EmptyState title={needle ? 'No dataset matches' : 'No datasets yet'}>
          {needle ? (
            'Try fewer letters.'
          ) : (
            <>
              A dataset is every table tagged with the same <code>dataset</code> value, such as <code>dataset=crm</code>. Tag a table that way and
              its dataset shows up here.
            </>
          )}
        </EmptyState>
      ) : data ? (
        <div className={loading ? 'overflow-x-auto rounded-lg border border-gray-200 bg-white opacity-60' : 'overflow-x-auto rounded-lg border border-gray-200 bg-white'}>
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Dataset</th>
                <th scope="col" className="px-3 py-2 font-medium">Catalogs</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Tables and views</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((d) => (
                <tr key={d.name} className="border-t border-gray-100 align-top hover:bg-gray-50">
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={datasetPath(d.name)} className="break-all font-mono text-sm font-medium text-gray-900 hover:text-primary hover:underline">
                        {d.name}
                      </Link>
                      {d.out_of_sync_count > 0 && <DriftBadge title={`${plural(d.out_of_sync_count, 'table')} in this dataset differ from Unity Catalog.`} />}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-gray-600" title={d.catalogs.join(', ')}>
                    {d.catalogs.slice(0, SHOWN_CATALOGS).join(', ')}
                    {d.catalogs.length > SHOWN_CATALOGS && <span className="font-sans text-gray-500"> +{d.catalogs.length - SHOWN_CATALOGS} more</span>}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums text-gray-500">{d.table_count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
