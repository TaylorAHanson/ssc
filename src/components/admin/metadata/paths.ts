export const METADATA_BASE = '/governance/metadata';

const enc = (part: string) => encodeURIComponent(part);

/** The browse page for a catalog or schema, or the catalog list with no arguments. */
export function browsePath(catalog?: string | null, schema?: string | null): string {
  if (!catalog) return METADATA_BASE;
  return schema ? `${METADATA_BASE}/browse/${enc(catalog)}/${enc(schema)}` : `${METADATA_BASE}/browse/${enc(catalog)}`;
}

/** The page for any object; a column opens its table with the column highlighted. */
export function objectPath(name: string, column?: string | null): string {
  const [catalog, schema, ...rest] = name.split('.');
  if (!rest.length) return browsePath(catalog, schema);
  const page = `${METADATA_BASE}/browse/${enc(catalog)}/${enc(schema)}/${enc(rest.join('.'))}`;
  return column ? `${page}?column=${enc(column)}` : page;
}

export const historyPath = `${METADATA_BASE}/history`;
export const adminPath = `${METADATA_BASE}/admin`;
