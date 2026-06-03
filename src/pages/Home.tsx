/**
 * Self Service / Home page.
 *
 * Same streaming chat surface as the Ask Your Data page (`<ChatView>`),
 * just with the Self Service / Governance / FinOps modes plumbed in
 * and a richer welcome that surfaces the discovery cards. The
 * mode-specific suggestion grid and the `autoQuery` deep-link
 * behavior live here; everything else (streaming, tool pills,
 * pending-poll lifecycle, form-route CTA, mode picker) is owned by
 * `ChatView`.
 *
 * The previous structured Q&A flow (`follow_up_questions` + radio /
 * multi-select widgets) was removed — the backend has not produced
 * those questions in a long time, the UI was dead code, and the
 * streaming endpoint emits a `route` event that the chat surface
 * already renders as a "Continue to form" CTA.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { BarChart3, Shield, Sparkles } from 'lucide-react';

import { ChatView, type ChatModeOption, type ChatRouteInfo, type ChatViewHandle } from '../components/chat/ChatView';
import { AgentWelcome } from '../components/chat/AgentWelcome';
import { useUserStore } from '../stores/userStore';
import { useBrandingStore } from '../stores/brandingStore';
import type { UserPersona } from '../types';

type AgentMode = 'Self Service Agent' | 'Governance' | 'FinOps';

const STORAGE_KEY = 'atlas_agent_mode';

const MODE_ICONS: Record<AgentMode, React.ReactNode> = {
    'Self Service Agent': <Sparkles className="w-3.5 h-3.5" />,
    Governance: <Shield className="w-3.5 h-3.5" />,
    FinOps: <BarChart3 className="w-3.5 h-3.5" />,
};

// Larger glyphs for the welcome header circle (the MODE_ICONS above are
// sized for the inline mode picker).
const MODE_WELCOME_ICONS: Record<AgentMode, React.ReactNode> = {
    'Self Service Agent': <Sparkles className="w-7 h-7 text-primary" />,
    Governance: <Shield className="w-7 h-7 text-primary" />,
    FinOps: <BarChart3 className="w-7 h-7 text-primary" />,
};

const MODE_PERMISSIONS: Record<AgentMode, UserPersona[]> = {
    'Self Service Agent': [
        'Platform Admin',
        'User',
        'Governance Admin',
        'Finance Admin',
        'Security Admin',
    ],
    Governance: ['Platform Admin', 'Governance Admin', 'Security Admin'],
    FinOps: ['Platform Admin', 'Finance Admin'],
};

interface ModeWelcome {
    title: string;
    description: string;
    example: string;
}

// One emphasized, non-clickable example per mode. Keep these open-ended
// so they hint at the kind of question the agent handles without making
// it feel like a fixed menu of options.
const MODE_WELCOME: Record<AgentMode, ModeWelcome> = {
    'Self Service Agent': {
        title: 'Self Service Agent',
        description:
            'Request access, provision resources, and get things done across data and platform — just describe what you need in plain language.',
        example: 'I need read access to the prod.sales.orders table',
    },
    Governance: {
        title: 'Governance Agent',
        description:
            'Audit access, monitor activity, and investigate compliance across the platform — ask in plain language.',
        example: 'Which users have admin access they don’t need?',
    },
    FinOps: {
        title: 'FinOps Agent',
        description:
            'Understand spend, forecast costs, and find savings across your workspaces — ask in plain language.',
        example: 'What were my most expensive workspaces last month?',
    },
};

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
    const currentPersona = useUserStore((state) => state.currentPersona);
    const isInitialized = useUserStore((state) => state.isInitialized);
    const { features } = useBrandingStore();

    const [agentMode, setAgentMode] = useState<AgentMode>(() => {
        const saved =
            typeof window !== 'undefined'
                ? (window.localStorage.getItem(STORAGE_KEY) as AgentMode | null)
                : null;
        return saved ?? 'Self Service Agent';
    });

    const navigate = useNavigate();
    const location = useLocation();
    const chatRef = useRef<ChatViewHandle | null>(null);

    // Persist mode selection so the user lands in the same context
    // when they revisit the page.
    useEffect(() => {
        if (typeof window === 'undefined') return;
        try {
            window.localStorage.setItem(STORAGE_KEY, agentMode);
        } catch {
            /* storage quota / disabled — non-fatal */
        }
    }, [agentMode]);

    // Reset mode if the active persona / feature flags forbid the
    // current selection. Same guard that lived in the prior Home
    // implementation.
    useEffect(() => {
        if (!isInitialized || !currentPersona) return;
        const allowedByPersona = MODE_PERMISSIONS[agentMode]?.includes(currentPersona);
        const allowedByFeature =
            (agentMode === 'Self Service Agent' && features?.self_service !== false) ||
            (agentMode === 'Governance' && features?.governance !== false) ||
            (agentMode === 'FinOps' && features?.finops !== false);
        if (!allowedByPersona || !allowedByFeature) {
            setAgentMode('Self Service Agent');
        }
    }, [currentPersona, agentMode, isInitialized, features]);

    const availableModes: ChatModeOption[] = useMemo(() => {
        return (Object.keys(MODE_PERMISSIONS) as AgentMode[])
            .filter((mode) => {
                if (!MODE_PERMISSIONS[mode].includes(currentPersona)) return false;
                if (mode === 'Self Service Agent' && features?.self_service === false) return false;
                if (mode === 'Governance' && features?.governance === false) return false;
                if (mode === 'FinOps' && features?.finops === false) return false;
                return true;
            })
            .map((mode) => ({ id: mode, label: mode, icon: MODE_ICONS[mode] }));
    }, [currentPersona, features]);

    // External deep-links can navigate to /request with
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

    const welcome = MODE_WELCOME[agentMode];
    const welcomeNode = (
        <AgentWelcome
            title={welcome.title}
            description={welcome.description}
            example={welcome.example}
            icon={MODE_WELCOME_ICONS[agentMode]}
        />
    );

    return (
        <div className="px-6 py-4 h-[calc(100vh-3rem)] flex flex-col">
            <div className="flex-1 min-h-0">
                <ChatView
                    ref={chatRef}
                    mode={agentMode}
                    welcomeNode={welcomeNode}
                    placeholder="Type your message..."
                    storageKey={`chatview_messages_self_service_${agentMode}`}
                    availableModes={availableModes}
                    onModeChange={(id) => setAgentMode(id as AgentMode)}
                    onRoute={handleRoute}
                    formCtaLabelFor={getButtonLabel}
                />
            </div>
        </div>
    );
}
