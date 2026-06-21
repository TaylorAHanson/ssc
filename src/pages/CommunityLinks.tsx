/**
 * Community Links — a fully config-driven page of curated external resources.
 *
 * Categories and links come from `configuration.yaml › community_links` (served
 * via /branding), so each customer curates their own resources/tools without
 * touching code. Icons are lucide names resolved through `renderNavIcon`.
 */
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { ExternalLink } from 'lucide-react';
import { renderNavIcon } from '../lib/navIcons';
import { useBrandingStore } from '../stores/brandingStore';
import type { CommunityLinkItem } from '../services/api';

/**
 * Accept either the full object form or a compact shorthand string
 * `"Title | URL | icon | description"` (icon/description optional). Returns null
 * for entries missing the two required pieces (title + url).
 */
function normalizeLink(raw: CommunityLinkItem | string): CommunityLinkItem | null {
  if (typeof raw === 'string') {
    const [title, url, icon, description] = raw.split('|').map((s) => s.trim());
    if (!title || !url) return null;
    return { title, url, icon: icon || undefined, description: description || undefined };
  }
  return raw && raw.title && raw.url ? raw : null;
}

export function CommunityLinks() {
  const communityLinks = useBrandingStore((s) => s.communityLinks);

  const categories = (communityLinks.categories || [])
    .map((c) => ({
      ...c,
      _links: (c?.links || []).map(normalizeLink).filter((l): l is CommunityLinkItem => !!l),
    }))
    .filter((c) => c && c.name && c._links.length > 0);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Community Links</h1>
        <p className="text-gray-600">
          Quick access to essential resources, tools, and documentation
        </p>
      </div>

      {categories.length === 0 ? (
        <Card className="bg-gray-50">
          <CardContent className="py-12 text-center text-gray-500">
            No community links are configured yet. An administrator can add them
            under <code className="text-gray-700">community_links</code> in the
            configuration.
          </CardContent>
        </Card>
      ) : (
        categories.map((category) => {
          const links = category._links;
          if (links.length === 0) return null;
          return (
            <div key={category.id || category.name}>
              <div className="flex items-center gap-2 mb-4">
                <div className="text-primary">
                  {renderNavIcon(category.icon || 'Link', 'w-5 h-5')}
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-gray-900">{category.name}</h2>
                  {category.description && (
                    <p className="text-sm text-gray-500">{category.description}</p>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {links.map((link) => (
                  <Card
                    key={link.title}
                    className="hover:shadow-lg transition-shadow cursor-pointer group"
                  >
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block"
                    >
                      <CardHeader>
                        <div className="flex items-start justify-between">
                          <div className="p-3 bg-primary/10 rounded-lg text-primary group-hover:bg-primary/20 transition-colors">
                            {renderNavIcon(link.icon || category.icon || 'Link', 'w-6 h-6')}
                          </div>
                          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-primary transition-colors" />
                        </div>
                        <CardTitle className="text-lg mt-4">{link.title}</CardTitle>
                      </CardHeader>
                      {link.description && (
                        <CardContent>
                          <p className="text-sm text-gray-600">{link.description}</p>
                        </CardContent>
                      )}
                    </a>
                  </Card>
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
