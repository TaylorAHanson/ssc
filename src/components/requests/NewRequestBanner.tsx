import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, WandSparkles, X } from 'lucide-react';
import { Button } from '../ui/button';

const NEW_REQUEST_BANNER_DISMISSED_KEY = 'requests.newRequestBanner.dismissed';

/**
 * Prominent call-to-action steering users to the unified chat agent to start a
 * new request. New requests are conversational (there's no standalone form), so
 * this banner is the primary entry point from the My Requests page. Dismissible:
 * the choice is persisted to localStorage so it doesn't reappear every visit.
 */
export function NewRequestBanner() {
  const navigate = useNavigate();
  const agentLabel = 'Ask Anything';

  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(NEW_REQUEST_BANNER_DISMISSED_KEY) === '1'
  );

  if (dismissed) return null;

  const handleDismiss = () => {
    localStorage.setItem(NEW_REQUEST_BANNER_DISMISSED_KEY, '1');
    setDismissed(true);
  };

  return (
    <div className="relative overflow-hidden rounded-xl bg-nav-bg text-nav-text shadow-md">
      {/* Decorative oversized icon — purely visual, hidden from a11y tree */}
      <WandSparkles
        aria-hidden="true"
        className="pointer-events-none absolute -right-5 -bottom-7 w-32 h-32 text-white/5"
      />
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="absolute right-2 top-2 rounded-md p-1 text-nav-text-muted transition-colors hover:bg-white/10 hover:text-white"
      >
        <X className="h-4 w-4" />
      </button>
      <div className="relative flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 pr-6">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/10">
            <WandSparkles className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold">Need to request something?</h2>
            <p className="text-xs text-white/80">
              New requests start as a conversation — tell the {agentLabel} what you need (data
              access, a workspace, a tag change) and it will submit it for you.
            </p>
          </div>
        </div>
        <Button
          onClick={() => navigate('/')}
          size="sm"
          className="shrink-0 self-start bg-white font-semibold text-nav-bg hover:bg-white/90 sm:self-auto"
        >
          New request
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
