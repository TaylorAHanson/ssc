import { Link, useParams } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import { useBrandingStore } from '../stores/brandingStore';
import { resolveNavIcon } from '../lib/navIcons';

// Renders a configured companion app inside an iframe so users can move
// between it and this app without leaving the tab. Driven entirely by the
// `embedded_apps:` list in configuration.yaml (resolved into the branding
// store), keyed by the `:appId` route param.
//
// Many Databricks-hosted targets (and other apps) send
// `X-Frame-Options: SAMEORIGIN` or a strict `Content-Security-Policy:
// frame-ancestors`, which browsers honor by rendering the iframe blank.
// There's no reliable way to detect that from the parent frame, so we always
// surface a small "open in a new tab" affordance as a fallback.
export function EmbeddedApp() {
  const { appId } = useParams<{ appId: string }>();
  const embeddedApps = useBrandingStore((s) => s.embeddedApps);
  const app = embeddedApps.find((a) => a.id === appId);

  if (!app) {
    const Icon = resolveNavIcon(undefined);
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12 text-gray-600">
        <Icon className="w-10 h-10 mb-3 text-gray-400" />
        <h2 className="text-lg font-semibold text-heading mb-1">
          This embedded app is not configured
        </h2>
        <p className="text-sm max-w-md">
          Add an entry under{' '}
          <code className="px-1 py-0.5 rounded bg-gray-100 text-xs">embedded_apps</code>{' '}
          in{' '}
          <code className="px-1 py-0.5 rounded bg-gray-100 text-xs">configuration.yaml</code>{' '}
          (with a matching{' '}
          <code className="px-1 py-0.5 rounded bg-gray-100 text-xs">id</code>) and
          restart the backend to enable this view.
        </p>
        <Link
          to="/request"
          className="mt-6 inline-flex items-center gap-2 px-4 py-2 rounded-md bg-accent text-white text-sm hover:opacity-90 transition-opacity"
        >
          Back to Ask Anything
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Slim header gives users a reliable escape hatch when the embed is
          blocked by the target's frame-ancestors / X-Frame-Options policy
          and the iframe renders blank. */}
      <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-2 border-b border-gray-200 bg-white">
        <span className="text-sm font-semibold text-heading truncate">
          {app.title}
        </span>
        <a
          href={app.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs text-gray-500 hover:text-accent transition-colors shrink-0"
          title="Open in a new tab"
        >
          Open in new tab
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      </div>
      <iframe
        key={app.url}
        src={app.url}
        title={app.title}
        className="block flex-1 w-full border-0 bg-white"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}
