/**
 * Unified chat landing page.
 *
 * A single "brain" agent (no modes) rendered through the shared
 * `<ChatView>` streaming surface. Tools are gated by the user's role on
 * the backend, so this page just renders one chat. The `autoQuery`
 * deep-link behavior lives here; everything else (streaming, tool pills,
 * pending-poll lifecycle, form-route CTA) is owned by `ChatView`.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Sparkles, Sun, Sunrise, Moon } from 'lucide-react';

import { ChatView, type ChatRouteInfo, type ChatViewHandle } from '../components/chat/ChatView';
import { AgentWelcome } from '../components/chat/AgentWelcome';
import { api } from '../services/api';
import { useUserStore } from '../stores/userStore';
import { useBrandingStore } from '../stores/brandingStore';
import { CatalogRails } from '../components/discover/CatalogRails';
import { prefetchCatalog } from '../lib/catalogCache';
import { buildDiscoverLink } from '../lib/discoverLinks';
import { getRecentUserTopics } from '../lib/chatPersistence';

const STORAGE_KEY = 'chatview_messages_unified';

function getButtonLabel(path: string): string {
    if (!path) return 'Continue to form';
    if (path.includes('/community/links') || path === '/community-links') {
        return 'Go to community links';
    }
    if (path.includes('/community/assets') || path === '/reusable-assets') {
        return 'View reusable assets';
    }
    if (path.includes('/community/training') || path === '/training') {
        return 'Go to training';
    }
    if (path.includes('/community/events') || path === '/events') {
        return 'View events';
    }
    const parts = path.split('/').filter(Boolean);
    if (parts.length === 0) return 'Continue';
    const last = parts[parts.length - 1];
    return `Go to ${last.replace(/-/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}`;
}

export function Home() {
    const navigate = useNavigate();
    const location = useLocation();
    const chatRef = useRef<ChatViewHandle | null>(null);

    const currentPersona = useUserStore((s) => s.currentPersona);
    const currentUser = useUserStore((s) => s.currentUser);
    const isInitialized = useUserStore((s) => s.isInitialized);
    const onboardingEnabled = useBrandingStore((s) => s.features?.onboarding_suggestions !== false);
    const homeCatalogEnabled = useBrandingStore((s) => s.features?.home_data_catalog !== false);

    // Personalized greeting: the user's first name + a time-of-day salutation
    // and matching icon. Derived from full_name (falling back to the email
    // local part), title-cased.
    const firstName = useMemo(() => {
        const raw = (currentUser?.full_name || currentUser?.email?.split('@')[0] || '').trim();
        const first = raw.split(/[ .]+/)[0];
        return first ? first.charAt(0).toUpperCase() + first.slice(1) : '';
    }, [currentUser]);
    const { greeting, TimeIcon } = useMemo(() => {
        const h = new Date().getHours();
        if (h < 12) return { greeting: 'Good morning', TimeIcon: Sunrise };
        if (h < 18) return { greeting: 'Good afternoon', TimeIcon: Sun };
        return { greeting: 'Good evening', TimeIcon: Moon };
    }, []);
    const [suggestions, setSuggestions] = useState<string[]>([]);

    // Branding-driven header, using the compact `short_name` (e.g. "edh").
    // Shown only with the "enhanced landing page" feature, which is off by
    // default so the landing isn't crowded with the brand title.
    const brandShortName = useBrandingStore((s) => s.brandShortName);
    const brandLogoUrl = useBrandingStore((s) => s.brandLogoUrl);
    const enhancedLandingPage = useBrandingStore((s) => s.features.enhanced_landing_page === true);

    // Pre-prompting: once we know who the user is, fetch a few personalized
    // starting prompts. Cached per session+persona so it's a single call per
    // login; failures silently fall back to the static welcome examples.
    useEffect(() => {
        if (!isInitialized || !onboardingEnabled) return;
        let cancelled = false;
        const cacheKey = `home_suggestions_${currentPersona}`;

        try {
            const cached = window.sessionStorage.getItem(cacheKey);
            if (cached) {
                const parsed = JSON.parse(cached);
                if (Array.isArray(parsed) && parsed.length > 0) {
                    setSuggestions(parsed);
                    return;
                }
            }
        } catch {
            /* ignore cache read errors */
        }

        // Recent topics come from the server-side transcript (falling back to the
        // local cache), so suggestions are personalized even on a device this
        // user has never chatted from.
        getRecentUserTopics(STORAGE_KEY)
            .then((topics) => api.getAgentSuggestions(topics))
            .then((res) => {
                if (cancelled) return;
                const prompts = (res.suggestions || []).map((s) => s.prompt).filter(Boolean);
                setSuggestions(prompts);
                try {
                    window.sessionStorage.setItem(cacheKey, JSON.stringify(prompts));
                } catch {
                    /* storage disabled — non-fatal */
                }
            })
            .catch(() => {
                /* leave the static welcome examples in place */
            });

        return () => {
            cancelled = true;
        };
    }, [isInitialized, currentPersona, onboardingEnabled]);

    // External deep-links can navigate to `/` (or `/request`) with
    // `state.autoQuery` to kick off a turn immediately. Forward that
    // into ChatView via the imperative handle, then clear the state
    // so a refresh doesn't replay the same query.
    useEffect(() => {
        const auto = location.state?.autoQuery as string | undefined;
        if (!auto) return;
        navigate(location.pathname, { replace: true, state: {} });
        // Tiny defer so the imperative ref is wired before submission.
        const id = window.setTimeout(() => {
            chatRef.current?.submitQuery(auto);
        }, 0);
        return () => window.clearTimeout(id);
    }, [location.state, location.pathname, navigate]);

    // Warm the catalog cache while the user reads the landing so that
    // navigating into Discover ("Browse all") renders without a cold spinner.
    useEffect(() => {
        prefetchCatalog();
    }, []);

    const handleRoute = (route: ChatRouteInfo) => {
        navigate(route.path);
    };

    // Default (pre-merge) welcome is a single "How can I help you today?"
    // prompt. The enhanced landing replaces it with the brand title + time-of-day
    // greeting + "Welcome, <name>" stack (gated on the feature flag).
    const welcomeNode = enhancedLandingPage ? (
        <div className="flex flex-col items-center justify-center pt-3 pb-6 gap-1.5 text-center">
            <div className="max-w-xl">
                {firstName ? (
                    <>
                        <p className="flex items-center justify-center gap-1.5 text-sm font-medium text-gray-500">
                            <TimeIcon className="w-4 h-4 text-primary" />
                            {greeting},
                        </p>
                        <h2 className="mt-0.5 text-2xl font-bold text-gray-900">
                            Welcome, {firstName}
                        </h2>
                        <p className="mt-2 flex items-center justify-center gap-2 text-base text-gray-500">
                            <Sparkles className="w-5 h-5 text-primary" />
                            How can I help you today?
                        </p>
                    </>
                ) : (
                    <h2 className="flex items-center justify-center gap-2 text-xl font-semibold text-gray-900">
                        <Sparkles className="w-6 h-6 text-primary" />
                        How can I help you today?
                    </h2>
                )}
            </div>
        </div>
    ) : (
        <AgentWelcome
            title="How can I help you today?"
            icon={<Sparkles className="w-7 h-7 text-primary" />}
        />
    );

    return (
        <div className="px-6 py-4 h-full min-h-0 flex flex-col">
            {/* Branded header (logo + short name). Part of the "enhanced landing
                page" and hidden by default (the sidebar already brands). */}
            {enhancedLandingPage && (
            <div className="flex flex-col items-center gap-2 pt-1 pb-2 shrink-0">
                <div className="flex items-center gap-2.5">
                    {brandLogoUrl && (
                        <img
                            src={brandLogoUrl}
                            alt=""
                            className="h-9 w-auto max-w-[140px] object-contain"
                        />
                    )}
                    <h1 className="text-2xl font-bold text-primary tracking-tight">
                        {brandShortName}
                    </h1>
                </div>
            </div>
            )}

            <div className="flex-1 min-h-0">
                <ChatView
                    ref={chatRef}
                    welcomeNode={welcomeNode}
                    placeholder="Ask a question..."
                    storageKey={STORAGE_KEY}
                    samplePrompts={suggestions.length > 0 ? suggestions : undefined}
                    onRoute={handleRoute}
                    formCtaLabelFor={getButtonLabel}
                    emptyStateExtras={
                        homeCatalogEnabled ? (
                            <div className="max-w-5xl mx-auto w-full px-1 pb-6">
                                <CatalogRails
                                    onViewDetails={(ref) => navigate(buildDiscoverLink({ asset: ref.id }))}
                                    onBrowseAll={() => navigate(buildDiscoverLink())}
                                />
                            </div>
                        ) : null
                    }
                />
            </div>
        </div>
    );
}
