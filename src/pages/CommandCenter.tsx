import { Link } from 'react-router-dom';
import { LayoutDashboard } from 'lucide-react';
import { useBrandingStore } from '../stores/brandingStore';

// Renders the companion Command Center app inside an iframe so users can
// move between the two without leaving this tab. Many Databricks-hosted
// targets (and other apps) send `X-Frame-Options: SAMEORIGIN` or a strict
// `Content-Security-Policy: frame-ancestors`, in which case the iframe
// will render blank — there's no in-page chrome to fall back to, so the
// user would need to switch tabs to debug. Intentional, to keep the
// embedded experience feeling truly native.
export function CommandCenter() {
  const commandCenterUrl = useBrandingStore((s) => s.commandCenterUrl);

  if (!commandCenterUrl) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12 text-gray-600">
        <LayoutDashboard className="w-10 h-10 mb-3 text-gray-400" />
        <h2 className="text-lg font-semibold text-heading mb-1">
          Command Center is not configured
        </h2>
        <p className="text-sm max-w-md">
          Set <code className="px-1 py-0.5 rounded bg-gray-100 text-xs">branding.command_center_url</code>{' '}
          in <code className="px-1 py-0.5 rounded bg-gray-100 text-xs">configuration.yaml</code>{' '}
          and restart the backend to enable this view.
        </p>
        <Link
          to="/request"
          className="mt-6 inline-flex items-center gap-2 px-4 py-2 rounded-md bg-accent text-white text-sm hover:opacity-90 transition-opacity"
        >
          Back to Request
        </Link>
      </div>
    );
  }

  return (
    <iframe
      key={commandCenterUrl}
      src={commandCenterUrl}
      title="Command Center"
      className="block h-full w-full border-0 bg-white"
      allow="clipboard-read; clipboard-write"
    />
  );
}
