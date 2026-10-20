import { createContext, useContext } from 'react';
import type { ToastMessage } from '../../rbac/ui';
import type { KeyInfo, MetadataConfig, WhoAmI } from '../../../types/metadata';

export interface MetadataContextValue {
  whoami: WhoAmI;
  config: MetadataConfig;
  isAdmin: boolean;
  /** Tag keys in use or governed, for autocomplete. */
  keys: KeyInfo[];
  /** Bumped after anything is saved so lists reload. */
  refreshToken: number;
  bumpRefresh: () => void;
  openObject: (name: string, column?: string | null) => void;
  openChangeSet: (id: string) => void;
  openRun: (id: string) => void;
  notify: (text: string, opts?: Pick<ToastMessage, 'link' | 'kind'>) => void;
}

export const MetadataContext = createContext<MetadataContextValue | null>(null);

export function useMetadata(): MetadataContextValue {
  const ctx = useContext(MetadataContext);
  if (!ctx) throw new Error('useMetadata must be used inside the Metadata Manager');
  return ctx;
}
