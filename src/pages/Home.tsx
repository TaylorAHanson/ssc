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
import {
    Activity,
    BarChart3,
    Box,
    Database,
    FileText,
    Info,
    Shield,
    Sparkles,
    TrendingUp,
} from 'lucide-react';

import { ChatView, type ChatModeOption, type ChatRouteInfo, type ChatViewHandle } from '../components/chat/ChatView';
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

interface DiscoveryItem {
    title: string;
    description: string;
    query: string;
}

interface DiscoveryColumn {
    title: string;
    icon: React.ReactNode;
    colorClass: string;
    hoverBorderClass: string;
    hoverTextClass: string;
    items: DiscoveryItem[];
}

const DISCOVERY_CONTENT: Record<AgentMode, DiscoveryColumn[]> = {
    'Self Service Agent': [
        {
            title: 'Data Access',
            icon: <Database className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Request Data Access', description: 'Access via Catalog, Schema, or Table', query: 'I need to request access to a dataset' },
                { title: 'REST API Access', description: 'Programmatic data access', query: 'I need REST API access to data' },
                { title: 'My Groups', description: 'What groups am I in?', query: 'What groups am I in?' },
                { title: 'My Current Access', description: 'What access do I have now?', query: 'What access do I have now?' },
            ],
        },
        {
            title: 'Enterprise Data',
            icon: <Database className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Discover Enterprise Data', description: 'Search and explore data assets', query: 'I want to discover enterprise data' },
                { title: 'Marketplace Certification', description: 'Certify assets for broader consumption', query: 'I need to certify a dataset for the marketplace' },
                { title: 'Learn About Data Quality', description: 'Find out how data quality is managed and how you can use it', query: 'I want to learn about data quality' },
            ],
        },
        {
            title: 'Platform Services',
            icon: <Box className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Workspace Access', description: 'Join an existing workspace', query: 'I need access to a Databricks workspace' },
                { title: 'Provision Workspace', description: 'Create a new Databricks environment', query: 'I need to provision a new Databricks workspace' },
                { title: 'Create Catalog or Schema', description: 'Create new data containers', query: 'I need to create a new catalog or schema' },
                { title: 'Service Principal', description: 'For external apps, automation, and CI/CD pipelines', query: 'I need a service principal for CI/CD' },
                { title: 'GitHub Repository', description: 'Create a new code repository', query: 'I need to create a new GitHub repository' },
            ],
        },
    ],
    Governance: [
        {
            title: 'Compliance Audit',
            icon: <Shield className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Overprovisioned Admins', description: 'Find users with excessive access', query: 'Which users are overprovisioned?' },
                { title: 'Orphaned Assets', description: 'Resources with deleted owners', query: 'Identify assets owned by deleted users or service principals' },
                { title: 'Missing Owners', description: 'Catalogs without assignment', query: 'Find catalogs and schemas that do not have an owner' },
            ],
        },
        {
            title: 'Activity Monitoring',
            icon: <Activity className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Failed Logins', description: 'Count failed attempts last 24h', query: 'Count failed logins in the last 24 hours' },
                { title: 'Unique Users', description: 'Daily active user count', query: 'How many unique users accessed the platform today?' },
                { title: 'Admin Changes', description: 'Recent privilege grants', query: 'Show recent administrative changes to groups or permissions' },
            ],
        },
        {
            title: 'Audit & Tracking',
            icon: <FileText className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Access Report', description: 'See who can access your data', query: 'Show me an access report for my production data' },
                { title: 'Usage Audit', description: 'Review recent administrative actions', query: 'Audit administrative actions in my workspace' },
            ],
        },
    ],
    FinOps: [
        {
            title: 'Cost Analysis',
            icon: <BarChart3 className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Top Spending', description: 'Highest cost workspaces', query: 'Which workspaces are the most expensive?' },
                { title: 'Forecast Spend', description: 'Predict future monthly cost', query: 'What is my predicted spend for next month?' },
                { title: 'Department Billing', description: 'Breakdown by cost center', query: 'Show me the cost breakdown by department' },
            ],
        },
        {
            title: 'Resource Efficiency',
            icon: <TrendingUp className="w-5 h-5" />,
            colorClass: 'text-primary',
            hoverBorderClass: 'hover:border-primary/50',
            hoverTextClass: 'group-hover:text-primary',
            items: [
                { title: 'Idle Clusters', description: 'Terminate unused compute', query: 'Identify idle clusters I can safely terminate' },
                { title: 'Tagging Compliance', description: 'Resources missing mandatory tags', query: 'Which users are out of compliance with the tagging policy?' },
            ],
        },
    ],
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

    const welcomeNode = (
        <div className="flex flex-col gap-6 max-w-5xl w-full mx-auto">
            <h2 className="text-2xl md:text-3xl font-semibold text-gray-800 text-center tracking-tight">
                What do you need to do today?
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-left">
                {DISCOVERY_CONTENT[agentMode].map((column, idx) => (
                    <div key={`${agentMode}-${idx}`} className="space-y-4">
                        <div className={`flex items-center gap-2 font-semibold ${column.colorClass}`}>
                            {column.icon}
                            <h3>{column.title}</h3>
                        </div>
                        <div className="grid gap-2">
                            {column.items.map((item) => (
                                <button
                                    key={item.title}
                                    type="button"
                                    onClick={() => chatRef.current?.submitQuery(item.query)}
                                    className={`relative p-2.5 rounded-xl border border-gray-200 hover:shadow-md hover:bg-white/80 transition-all duration-200 text-left group ${column.hoverBorderClass}`}
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <div
                                            className={`text-[13px] font-medium text-gray-900 transition-colors ${column.hoverTextClass}`}
                                        >
                                            {item.title}
                                        </div>
                                        <div className="relative group/info">
                                            <Info className="w-4 h-4 text-gray-400 hover:text-gray-600 transition-colors" />
                                            <div className="absolute bottom-full right-0 mb-2 w-48 p-2 bg-gray-900 text-white text-xs rounded-lg shadow-xl opacity-0 translate-y-2 invisible group-hover/info:opacity-100 group-hover/info:translate-y-0 group-hover/info:visible transition-all duration-200 z-50 pointer-events-none">
                                                {item.description}
                                                <div className="absolute top-full right-1.5 -mt-1 border-4 border-transparent border-t-gray-900" />
                                            </div>
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
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
