import { useState } from 'react';
import { Download, Upload } from 'lucide-react';
import { Button } from '../../ui/button';
import { AdoptDialog } from './AdoptDialog';
import { useMetadata } from './context';
import { DriftTab } from './DriftTab';
import { ImportDialog } from './ImportDialog';
import { SyncStatus } from './ModeBanner';
import { PrincipalsTab } from './PrincipalsTab';
import { Rollup } from './Rollup';
import { RunsTab } from './RunsTab';

/** Admin-only: whether syncing works, what's out of sync, sync history, automation access and bulk onboarding. */
export function AdminPage() {
  const { config } = useMetadata();
  const [dialog, setDialog] = useState<'adopt' | 'import' | null>(null);
  return (
    <div className="space-y-4">
      <SyncStatus />
      <Rollup title="Out of sync with Unity Catalog" hint="Tables whose tags in Unity Catalog differ from the ones saved here">
        <DriftTab />
      </Rollup>
      <Rollup title="Sync history" hint="Every check of Unity Catalog, and what it wrote" defaultOpen={config.circuit_breaker_tripped}>
        <RunsTab />
      </Rollup>
      <Rollup title="Automation access" hint="Pipelines and service principals allowed to set tags">
        <PrincipalsTab />
      </Rollup>
      <Rollup title="Bring in many tables at once">
        <div className="flex flex-wrap gap-3">
          <div className="max-w-sm space-y-1">
            <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('adopt')}>
              <Download className="h-3.5 w-3.5" /> Start managing catalogs
            </Button>
            <p className="text-xs text-gray-500">Saves what Unity Catalog has now as the starting point, so syncing protects it from here on.</p>
          </div>
          <div className="max-w-sm space-y-1">
            <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setDialog('import')}>
              <Upload className="h-3.5 w-3.5" /> Import from a file
            </Button>
            <p className="text-xs text-gray-500">Set tags and descriptions for many tables from a CSV or JSON file, previewed before anything is saved.</p>
          </div>
        </div>
      </Rollup>
      {dialog === 'adopt' && <AdoptDialog onClose={() => setDialog(null)} />}
      {dialog === 'import' && <ImportDialog onClose={() => setDialog(null)} />}
    </div>
  );
}
