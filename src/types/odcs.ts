// The subset of an ODCS data contract (parsed from its YAML) that the UI reads.
// The YAML is user-authored, so every field is optional and list fields are
// still checked with `Array.isArray` before use.
import yaml from 'js-yaml';

export interface OdcsCustomProperty {
  property?: string;
  value?: string | number | boolean | string[] | null;
}

export interface OdcsServer {
  id?: string;
  environment?: string;
  type?: string;
  catalog?: string;
  schema?: string;
  host?: string;
}

export interface OdcsColumn {
  id?: string;
  name?: string;
  businessName?: string;
  logicalType?: string;
  physicalType?: string;
  description?: string;
  primaryKey?: boolean;
  role?: string;
  required?: boolean;
  tags?: string[];
}

export interface OdcsTable {
  id?: string;
  name?: string;
  physicalName?: string;
  businessName?: string;
  physicalType?: string;
  description?: string;
  properties?: OdcsColumn[];
  tags?: string[];
  customProperties?: OdcsCustomProperty[];
}

export interface OdcsTeamMember {
  username?: string;
  name?: string;
  role?: string;
  dateIn?: string;
}

export interface OdcsSlaProperty {
  property?: string;
  value?: string | number;
  unit?: string;
}

export interface OdcsSupportChannel {
  channel?: string;
  url?: string;
  tool?: string;
  description?: string;
}

export interface OdcsAuthoritativeDefinition {
  type?: string;
  url?: string;
  description?: string;
}

export interface OdcsContract {
  id?: string;
  kind?: string;
  version?: string | number;
  status?: string;
  dataProduct?: string;
  dataProductOwner?: string;
  domain?: string;
  description?: { purpose?: string; limitations?: string; usage?: string };
  servers?: OdcsServer[];
  schema?: OdcsTable[];
  team?: OdcsTeamMember[];
  slaProperties?: OdcsSlaProperty[];
  support?: OdcsSupportChannel[];
  authoritativeDefinitions?: OdcsAuthoritativeDefinition[];
}

/** Parse contract YAML; anything other than a mapping yields null. Throws on invalid YAML. */
export function parseContractYaml(text: string): OdcsContract | null {
  const doc: unknown = yaml.load(text);
  return doc && typeof doc === 'object' && !Array.isArray(doc) ? (doc as OdcsContract) : null;
}
