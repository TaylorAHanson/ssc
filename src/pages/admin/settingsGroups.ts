// Client-rendered Settings pages that other screens link to. Must match the
// group names in settings_store.py.
export const LEGACY_DASHBOARDS_GROUP = 'Legacy Dashboards';
export const TRAINING_GROUP = 'Training';

export const settingsGroupPath = (group: string) => `/admin/settings?group=${encodeURIComponent(group)}`;
