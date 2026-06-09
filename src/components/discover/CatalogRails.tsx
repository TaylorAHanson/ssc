/**
 * Shared catalog "rails" used on both the agent landing and the Discover
 * page: Your Pinned Items → Data Products → (optional domains slot) →
 * Datasets. Items are pinnable (persisted per-user in localStorage) and
 * clicking a card calls `onAsk`. "Browse all" is delegated to the caller so
 * each surface can decide what it means (navigate vs. filter in place).
 */
import { useMemo, type ReactNode } from 'react';
import { ChevronRight, Pin } from 'lucide-react';

import { AssetCard, normalizeAssetType } from '../../lib/assetTypes';
import { usePinnedItems, pinKey, type PinnedItem } from '../../lib/usePinnedItems';
import { useUserStore } from '../../stores/userStore';
import { dataAssetsResource, odpsResource } from '../../lib/catalogCache';

export type BrowseAllTarget = 'data_product' | 'dataset';

export function CatalogRails({
  onAsk,
  onBrowseAll,
  domainsSlot,
}: {
  onAsk: (query: string) => void;
  onBrowseAll: (target: BrowseAllTarget) => void;
  /** Optional content rendered between the Data Products and Datasets rails. */
  domainsSlot?: ReactNode;
}) {
  const userEmail = useUserStore((s) => s.currentUser?.email);
  const { pinned, isPinned, togglePin, unpin } = usePinnedItems(userEmail || undefined);

  // Served from the shared catalog cache: instant on revisit, revalidated in
  // the background. Only real catalog data is shown — empty rails are hidden.
  const { data: odps } = odpsResource.useResource();
  const { data: assets } = dataAssetsResource.useResource();

  const dataProducts = useMemo(() => odps.slice(0, 4), [odps]);

  const datasets = useMemo(
    () => assets.filter((a) => normalizeAssetType(a.type) === 'dataset').slice(0, 4),
    [assets],
  );

  const askAbout = (title: string, typeLabel: string) =>
    onAsk(`Tell me about the "${title}" ${typeLabel}.`);

  const dpItem = (dp: any): PinnedItem => ({
    key: pinKey('data_product', dp.id),
    id: dp.id,
    type: 'data_product',
    title: dp.name,
    subtitle: dp.description || 'Governed data product',
  });

  const dsItem = (ds: any): PinnedItem => ({
    key: pinKey(ds.type, ds.id),
    id: ds.id,
    type: ds.type,
    title: ds.table_name || ds.name,
    subtitle: ds.description,
    certified: ds.certified,
  });

  return (
    <div className="space-y-8">
      {/* Your Pinned Items */}
      <section className="space-y-3">
        <h2 className="text-lg font-bold text-gray-900">Your Pinned Items</h2>
        {pinned.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50/50 p-6 text-center">
            <p className="text-sm text-gray-500 inline-flex items-center gap-1.5 justify-center flex-wrap">
              Pin data products and datasets with the
              <Pin className="w-3.5 h-3.5 text-gray-400" />
              icon to keep them here for quick access.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {pinned.map((item) => (
              <AssetCard
                key={item.key}
                type={item.type}
                title={item.title}
                subtitle={item.subtitle}
                certified={item.certified}
                pinned
                onTogglePin={() => unpin(item.key)}
                onClick={() => askAbout(item.title, item.type === 'data_product' ? 'data product' : 'dataset')}
              />
            ))}
          </div>
        )}
      </section>

      {/* Data Products */}
      {dataProducts.length > 0 && (
        <Rail title="Data Products" onBrowseAll={() => onBrowseAll('data_product')}>
          {dataProducts.map((dp) => {
            const item = dpItem(dp);
            return (
              <AssetCard
                key={dp.id}
                type="data_product"
                title={dp.name}
                subtitle={dp.description || 'Governed data product'}
                pinned={isPinned(item.key)}
                onTogglePin={() => togglePin(item)}
                onClick={() => askAbout(dp.name, 'data product')}
              />
            );
          })}
        </Rail>
      )}

      {/* Optional domains browser, slotted under Data Products. */}
      {domainsSlot}

      {/* Datasets */}
      {datasets.length > 0 && (
        <Rail title="Datasets" onBrowseAll={() => onBrowseAll('dataset')}>
          {datasets.map((ds) => {
            const item = dsItem(ds);
            return (
              <AssetCard
                key={ds.id}
                type={ds.type}
                title={ds.table_name || ds.name}
                subtitle={ds.description}
                certified={ds.certified}
                pinned={isPinned(item.key)}
                onTogglePin={() => togglePin(item)}
                onClick={() =>
                  askAbout(
                    ds.table_name || ds.name,
                    normalizeAssetType(ds.type) === 'dataset' ? 'dataset' : 'asset',
                  )
                }
              />
            );
          })}
        </Rail>
      )}
    </div>
  );
}

function Rail({
  title,
  onBrowseAll,
  children,
}: {
  title: string;
  onBrowseAll?: () => void;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between">
        <h2 className="text-lg font-bold text-gray-900">{title}</h2>
        {onBrowseAll && (
          <button
            type="button"
            onClick={onBrowseAll}
            className="text-sm text-primary hover:text-primary/80 font-medium flex items-center group"
          >
            Browse all
            <ChevronRight className="w-4 h-4 ml-0.5 group-hover:translate-x-0.5 transition-transform" />
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">{children}</div>
    </section>
  );
}
