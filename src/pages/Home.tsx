/**
 * Unified chat landing page.
 *
 * A single "brain" agent (no modes) rendered through the shared
 * `<ChatView>` streaming surface. Tools are gated by the user's role on
 * the backend, so this page just renders one chat. The `autoQuery`
 * deep-link behavior lives here; everything else (streaming, tool pills,
 * pending-poll lifecycle, form-route CTA) is owned by `ChatView`.
 */
import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';

import { ChatView, type ChatRouteInfo, type ChatViewHandle } from '../components/chat/ChatView';
import { AgentWelcome } from '../components/chat/AgentWelcome';
import { api } from '../services/api';
import { useUserStore } from '../stores/userStore';
import { useBrandingStore } from '../stores/brandingStore';

const STORAGE_KEY = 'chatview_messages_unified';

// Pull the user's most recent questions out of the persisted chat history so
// the backend can personalize starting suggestions. Best-effort and bounded.
function getRecentTopics(): string[] {
    try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        const texts = parsed
            .filter((m: { kind?: string; content?: unknown }) => m?.kind === 'user' && typeof m.content === 'string')
            .map((m: { content: string }) => m.content.trim())
            .filter(Boolean)
            .map((t: string) => t.slice(0, 140));
        return Array.from(new Set(texts)).slice(-5);
    } catch {
        return [];
    }
}

function getButtonLabel(path: string): string {
    if (!path) return 'Continue to form';
    if (path.startsWith('/paas/') || path.startsWith('/daas/')) {
        return 'Go to pre-filled form';
    }
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
    const isInitialized = useUserStore((s) => s.isInitialized);
    const onboardingEnabled = useBrandingStore((s) => s.features?.onboarding_suggestions !== false);
    const [suggestions, setSuggestions] = useState<string[]>([]);

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

        api.getAgentSuggestions(getRecentTopics())
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

    const handleRoute = (route: ChatRouteInfo) => {
        navigate(route.path);
    };

    const welcomeNode = (
        <AgentWelcome
            title="How can I help?"
            description="Ask about your data or make a request — just describe what you need in plain language."
            icon={<Sparkles className="w-7 h-7 text-primary" />}
        />
    );

    return (
        <div className="px-6 py-4 h-[calc(100vh-3rem)] flex flex-col">
            <div className="flex-1 min-h-0">
                <ChatView
                    ref={chatRef}
                    welcomeNode={welcomeNode}
                    placeholder="Type your message..."
                    storageKey={STORAGE_KEY}
                    samplePrompts={suggestions.length > 0 ? suggestions : undefined}
                    onRoute={handleRoute}
                    formCtaLabelFor={getButtonLabel}
                />
            </div>
        </div>
    );
}
