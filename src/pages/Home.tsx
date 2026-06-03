/**
 * Unified chat landing page.
 *
 * A single "brain" agent (no modes) rendered through the shared
 * `<ChatView>` streaming surface. Tools are gated by the user's role on
 * the backend, so this page just renders one chat. The `autoQuery`
 * deep-link behavior lives here; everything else (streaming, tool pills,
 * pending-poll lifecycle, form-route CTA) is owned by `ChatView`.
 */
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';

import { ChatView, type ChatRouteInfo, type ChatViewHandle } from '../components/chat/ChatView';
import { AgentWelcome } from '../components/chat/AgentWelcome';

const STORAGE_KEY = 'chatview_messages_unified';

// Two emphasized, non-clickable example hints spanning the agent's main
// buckets: exploring data and making requests. Kept open-ended so they
// hint at breadth without reading as a fixed menu.
const WELCOME_EXAMPLES = [
    { label: 'Data', text: 'How many active customers did we have last quarter?' },
    { label: 'Requests', text: 'I need read access to the prod.sales.orders table' },
];

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
        // Persist any prefill data the agent computed so the form page
        // can pick it up out of localStorage on mount.
        if (route.prefill && Object.keys(route.prefill).length > 0) {
            try {
                window.localStorage.setItem(
                    `form_prefill_${route.path}`,
                    JSON.stringify(route.prefill),
                );
            } catch {
                /* swallow */
            }
        }
        navigate(route.path);
    };

    const welcomeNode = (
        <AgentWelcome
            title="How can I help?"
            description="Ask about your data or make a request — just describe what you need in plain language."
            examples={WELCOME_EXAMPLES}
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
                    onRoute={handleRoute}
                    formCtaLabelFor={getButtonLabel}
                />
            </div>
        </div>
    );
}
