import { Button } from '../../components/ui/button';
import { Plus, Trash2, GripVertical } from 'lucide-react';
import type {
  SettingField,
  SelfServiceCatalog,
  SelfServiceCategory,
  SelfServiceCard,
  CommunityLinksCatalog,
  CommunityCategory,
  CommunityLink,
  EmbeddedApp,
} from '../../services/api';

// --- shared helpers --------------------------------------------------------

const inputCls =
  'w-full px-2.5 py-1.5 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200';

function replaceAt<T>(arr: T[], idx: number, next: T): T[] {
  return arr.map((x, i) => (i === idx ? next : x));
}
function removeAt<T>(arr: T[], idx: number): T[] {
  return arr.filter((_, i) => i !== idx);
}
function personasToStr(p?: string[]): string {
  return (p || []).join(', ');
}
function strToPersonas(s: string): string[] {
  // Keep as-typed (don't drop trailing blanks) so typing commas stays smooth;
  // the backend trims + drops empties on save.
  return s.split(',').map((x) => x.trim());
}

function FieldHeader({ field }: { field: SettingField }) {
  return (
    <div className="space-y-1">
      <label className="text-sm font-medium text-gray-800">{field.label}</label>
      {field.help && <p className="text-xs text-gray-500 max-w-2xl leading-relaxed">{field.help}</p>}
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  textarea,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  textarea?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-500">{label}</span>
      {textarea ? (
        <textarea
          className={`${inputCls} resize-y`}
          rows={2}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          className={inputCls}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}

function EnabledToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-gray-700">
      <button
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors ${
          value ? 'bg-primary' : 'bg-gray-300'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            value ? 'translate-x-6' : 'translate-x-1'
          }`}
        />
      </button>
      <span>{value ? 'Enabled' : 'Disabled'}</span>
    </label>
  );
}

function RemoveButton({ onClick, title }: { onClick: () => void; title: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="p-1.5 text-gray-400 hover:text-red-600 transition-colors flex-shrink-0"
      title={title}
    >
      <Trash2 className="w-4 h-4" />
    </button>
  );
}

// --- string list (web_search allowed_domains / sitemaps) -------------------

export function StringListField({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const items = value || [];
  return (
    <div className="space-y-3 pb-4 border-b border-gray-100 last:border-0">
      <FieldHeader field={field} />
      <div className="space-y-2 max-w-xl">
        {items.length === 0 && <p className="text-sm text-gray-400 italic">None yet.</p>}
        {items.map((item, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <input
              className={inputCls}
              value={item}
              onChange={(e) => onChange(replaceAt(items, idx, e.target.value))}
            />
            <RemoveButton onClick={() => onChange(removeAt(items, idx))} title="Remove" />
          </div>
        ))}
      </div>
      <Button variant="outline" size="sm" onClick={() => onChange([...items, ''])}>
        <Plus className="w-4 h-4 mr-1" /> {field.add_label || 'Add'}
      </Button>
    </div>
  );
}

// --- Self-Service Center ---------------------------------------------------

const EMPTY_SS_CARD: SelfServiceCard = { title: '' };
const EMPTY_SS_CATEGORY: SelfServiceCategory = { title: '', icon: '', cards: [] };

export function SelfServiceCenterEditor({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: SelfServiceCatalog;
  onChange: (v: SelfServiceCatalog) => void;
}) {
  const catalog: SelfServiceCatalog = value || { enabled: true, categories: [] };
  const categories = catalog.categories || [];

  const setCategories = (next: SelfServiceCategory[]) => onChange({ ...catalog, categories: next });
  const updateCategory = (ci: number, next: SelfServiceCategory) =>
    setCategories(replaceAt(categories, ci, next));

  return (
    <div className="space-y-4 pb-4 border-b border-gray-100 last:border-0">
      <div className="flex items-start justify-between gap-4">
        <FieldHeader field={field} />
        <EnabledToggle value={catalog.enabled ?? true} onChange={(v) => onChange({ ...catalog, enabled: v })} />
      </div>

      <div className="space-y-4">
        {categories.map((cat, ci) => {
          const cards = cat.cards || [];
          const setCards = (next: SelfServiceCard[]) => updateCategory(ci, { ...cat, cards: next });
          return (
            <div key={ci} className="rounded-lg border border-gray-200 bg-gray-50/60">
              <div className="flex items-center gap-2 p-3 border-b border-gray-200 bg-white rounded-t-lg">
                <GripVertical className="w-4 h-4 text-gray-300 flex-shrink-0" />
                <input
                  className={`${inputCls} font-medium`}
                  placeholder="Category title"
                  value={cat.title}
                  onChange={(e) => updateCategory(ci, { ...cat, title: e.target.value })}
                />
                <input
                  className={`${inputCls} w-40`}
                  placeholder="Icon (e.g. Database)"
                  value={cat.icon || ''}
                  onChange={(e) => updateCategory(ci, { ...cat, icon: e.target.value })}
                />
                <RemoveButton onClick={() => setCategories(removeAt(categories, ci))} title="Remove category" />
              </div>

              <div className="p-3 space-y-3">
                {cards.length === 0 && <p className="text-xs text-gray-400 italic">No cards in this category.</p>}
                {cards.map((card, ki) => (
                  <div key={ki} className="rounded-md border border-gray-200 bg-white p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      <input
                        className={`${inputCls} font-medium`}
                        placeholder="Card title"
                        value={card.title}
                        onChange={(e) => setCards(replaceAt(cards, ki, { ...card, title: e.target.value }))}
                      />
                      <RemoveButton onClick={() => setCards(removeAt(cards, ki))} title="Remove card" />
                    </div>
                    <TextField
                      label="Description"
                      value={card.description || ''}
                      placeholder="One-line blurb (optional)"
                      onChange={(v) => setCards(replaceAt(cards, ki, { ...card, description: v }))}
                    />
                    <div className="grid grid-cols-2 gap-2">
                      <TextField
                        label="Prompt (seeds the Assistant)"
                        value={card.prompt || ''}
                        placeholder="I need to request access…"
                        onChange={(v) => setCards(replaceAt(cards, ki, { ...card, prompt: v }))}
                      />
                      <TextField
                        label="Route (in-app link — wins over prompt)"
                        value={card.route || ''}
                        placeholder="/discovery"
                        onChange={(v) => setCards(replaceAt(cards, ki, { ...card, route: v }))}
                      />
                    </div>
                    <TextField
                      label="Allowed personas (comma-separated; blank = everyone)"
                      value={personasToStr(card.allowed_personas)}
                      placeholder="Platform Admin, Governance Admin"
                      onChange={(v) => setCards(replaceAt(cards, ki, { ...card, allowed_personas: strToPersonas(v) }))}
                    />
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setCards([...cards, { ...EMPTY_SS_CARD }])}>
                  <Plus className="w-4 h-4 mr-1" /> Add card
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      <Button variant="outline" size="sm" onClick={() => setCategories([...categories, { ...EMPTY_SS_CATEGORY }])}>
        <Plus className="w-4 h-4 mr-1" /> {field.add_label || 'Add category'}
      </Button>
    </div>
  );
}

// --- Community Links -------------------------------------------------------

const EMPTY_CL_LINK: CommunityLink = { title: '', url: '' };
const EMPTY_CL_CATEGORY: CommunityCategory = { name: '', icon: '', links: [] };

export function CommunityLinksEditor({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: CommunityLinksCatalog;
  onChange: (v: CommunityLinksCatalog) => void;
}) {
  const catalog: CommunityLinksCatalog = value || { enabled: true, categories: [] };
  const categories = catalog.categories || [];

  const setCategories = (next: CommunityCategory[]) => onChange({ ...catalog, categories: next });
  const updateCategory = (ci: number, next: CommunityCategory) => setCategories(replaceAt(categories, ci, next));

  return (
    <div className="space-y-4 pb-4 border-b border-gray-100 last:border-0">
      <div className="flex items-start justify-between gap-4">
        <FieldHeader field={field} />
        <EnabledToggle value={catalog.enabled ?? true} onChange={(v) => onChange({ ...catalog, enabled: v })} />
      </div>

      <div className="space-y-4">
        {categories.map((cat, ci) => {
          const links = cat.links || [];
          const setLinks = (next: CommunityLink[]) => updateCategory(ci, { ...cat, links: next });
          return (
            <div key={ci} className="rounded-lg border border-gray-200 bg-gray-50/60">
              <div className="flex items-center gap-2 p-3 border-b border-gray-200 bg-white rounded-t-lg">
                <GripVertical className="w-4 h-4 text-gray-300 flex-shrink-0" />
                <input
                  className={`${inputCls} font-medium`}
                  placeholder="Category name"
                  value={cat.name}
                  onChange={(e) => updateCategory(ci, { ...cat, name: e.target.value })}
                />
                <input
                  className={`${inputCls} w-40`}
                  placeholder="Icon (e.g. BookOpen)"
                  value={cat.icon || ''}
                  onChange={(e) => updateCategory(ci, { ...cat, icon: e.target.value })}
                />
                <RemoveButton onClick={() => setCategories(removeAt(categories, ci))} title="Remove category" />
              </div>

              <div className="p-3 space-y-2">
                {links.length === 0 && <p className="text-xs text-gray-400 italic">No links in this category.</p>}
                {links.map((link, li) => (
                  <div key={li} className="flex items-start gap-2">
                    <div className="grid grid-cols-2 gap-2 flex-1">
                      <input
                        className={inputCls}
                        placeholder="Title"
                        value={link.title}
                        onChange={(e) => setLinks(replaceAt(links, li, { ...link, title: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        placeholder="https://…"
                        value={link.url}
                        onChange={(e) => setLinks(replaceAt(links, li, { ...link, url: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        placeholder="Icon (optional)"
                        value={link.icon || ''}
                        onChange={(e) => setLinks(replaceAt(links, li, { ...link, icon: e.target.value }))}
                      />
                      <input
                        className={inputCls}
                        placeholder="Description (optional)"
                        value={link.description || ''}
                        onChange={(e) => setLinks(replaceAt(links, li, { ...link, description: e.target.value }))}
                      />
                    </div>
                    <RemoveButton onClick={() => setLinks(removeAt(links, li))} title="Remove link" />
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setLinks([...links, { ...EMPTY_CL_LINK }])}>
                  <Plus className="w-4 h-4 mr-1" /> Add link
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      <Button variant="outline" size="sm" onClick={() => setCategories([...categories, { ...EMPTY_CL_CATEGORY }])}>
        <Plus className="w-4 h-4 mr-1" /> {field.add_label || 'Add category'}
      </Button>
    </div>
  );
}

// --- Embedded Apps ---------------------------------------------------------

const EMPTY_EMBEDDED_APP: EmbeddedApp = { id: '', title: '', url: '' };

export function EmbeddedAppsEditor({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: EmbeddedApp[];
  onChange: (v: EmbeddedApp[]) => void;
}) {
  const apps = value || [];
  const update = (i: number, next: EmbeddedApp) => onChange(replaceAt(apps, i, next));

  return (
    <div className="space-y-4 pb-4 border-b border-gray-100 last:border-0">
      <FieldHeader field={field} />

      <div className="space-y-3">
        {apps.length === 0 && <p className="text-sm text-gray-400 italic">No embedded apps configured.</p>}
        {apps.map((app, i) => (
          <div key={i} className="rounded-lg border border-gray-200 bg-white p-3 space-y-2">
            <div className="flex items-center gap-2">
              <input
                className={`${inputCls} font-medium`}
                placeholder="Title"
                value={app.title}
                onChange={(e) => update(i, { ...app, title: e.target.value })}
              />
              <RemoveButton onClick={() => onChange(removeAt(apps, i))} title="Remove app" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <TextField
                label="Id (slug; blank = derived from title)"
                value={app.id}
                placeholder="command_center"
                onChange={(v) => update(i, { ...app, id: v })}
              />
              <TextField
                label="URL (iframe src, https)"
                value={app.url}
                placeholder="https://…"
                onChange={(v) => update(i, { ...app, url: v })}
              />
              <TextField
                label="Icon (optional)"
                value={app.icon || ''}
                placeholder="LayoutDashboard"
                onChange={(v) => update(i, { ...app, icon: v })}
              />
              <TextField
                label="Sidebar group (optional)"
                value={app.group || ''}
                placeholder="Build & Customize"
                onChange={(v) => update(i, { ...app, group: v })}
              />
            </div>
            <TextField
              label="Description (optional)"
              value={app.description || ''}
              onChange={(v) => update(i, { ...app, description: v })}
            />
            <TextField
              label="Allowed personas (comma-separated; blank = everyone)"
              value={personasToStr(app.allowed_personas)}
              placeholder="Platform Admin"
              onChange={(v) => update(i, { ...app, allowed_personas: strToPersonas(v) })}
            />
          </div>
        ))}
      </div>

      <Button variant="outline" size="sm" onClick={() => onChange([...apps, { ...EMPTY_EMBEDDED_APP }])}>
        <Plus className="w-4 h-4 mr-1" /> {field.add_label || 'Add app'}
      </Button>
    </div>
  );
}
