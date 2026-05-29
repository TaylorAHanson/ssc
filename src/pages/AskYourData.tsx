/**
 * "Ask Your Data" page — chat with Databricks Genie inline.
 *
 * Reuses the streaming `<ChatView>` component with `mode="ask_your_data"`.
 * The agent runner exposes only the `ask_your_data` tool in this mode,
 * so the LLM stays focused on forwarding questions to Databricks
 * Genie (the general-purpose data chat) rather than routing the user
 * into provisioning workflows.
 *
 * Includes an "Open in Databricks" button that deep-links to the
 * native Genie experience for power users who want richer
 * exploration than the embedded chat surface offers.
 */
import { ExternalLink, Sparkles } from 'lucide-react';
import { useMemo } from 'react';

import { ChatView } from '../components/chat/ChatView';
import { useBrandingStore } from '../stores/brandingStore';
import { genieHomeUrl } from '../lib/databricksLinks';

const SAMPLE_PROMPTS = [
    'What catalogs and schemas can I see?',
    'Who owns the table prod.sales.orders?',
    'How many active customers did we have last quarter?',
    "Compare this year's revenue by region against last year",
];

export function AskYourData() {
    const databricksWorkspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
    const genieFullExperienceUrl = useBrandingStore((s) => s.genieFullExperienceUrl);

    // Prefer the explicitly configured `genie_full_experience_url`;
    // fall back to the workspace's Genie home (`/one`) so something
    // sensible appears even without dedicated configuration.
    const fullExperienceUrl = useMemo(() => {
        if (genieFullExperienceUrl) return genieFullExperienceUrl;
        return genieHomeUrl(databricksWorkspaceUrl);
    }, [databricksWorkspaceUrl, genieFullExperienceUrl]);

    const welcomeNode = (
        <div className="flex flex-col items-center justify-center py-10 gap-4 text-center">
            <div className="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center">
                <Sparkles className="w-7 h-7 text-primary" />
            </div>
            <div>
                <h2 className="text-xl font-semibold text-gray-900">Ask Your Data</h2>
                <p className="text-sm text-gray-500 max-w-md mt-1">
                    Type a natural-language question about enterprise data. Answers are
                    grounded in the data you have access to and typically take 30 to
                    120 seconds.
                </p>
            </div>
        </div>
    );

    return (
        <div className="px-6 py-4 h-[calc(100vh-3rem)] flex flex-col">
            <div className="flex items-start justify-between gap-4 mb-4">
                <div>
                    <h1 className="text-xl font-semibold text-gray-900">Ask Your Data</h1>
                    <p className="text-sm text-gray-500 mt-0.5">
                        Chat with Databricks Genie.
                    </p>
                </div>
                {fullExperienceUrl && (
                    // Solid brand-blue CTA so the "full experience" escape
                    // hatch is visible — early users were missing the
                    // outline-only version. Matches the sidebar accent so
                    // it reads as a primary action, not a tertiary link.
                    <a
                        href={fullExperienceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white text-sm font-semibold shadow-sm hover:bg-primary/90 hover:shadow-md focus:outline-none focus:ring-4 focus:ring-primary/20 transition-all"
                    >
                        <ExternalLink className="w-4 h-4" />
                        Open in Databricks
                    </a>
                )}
            </div>

            <div className="flex-1 min-h-0">
                <ChatView
                    mode="ask_your_data"
                    welcomeNode={welcomeNode}
                    placeholder="Ask a question about your data..."
                    storageKey="chatview_messages_ask_your_data"
                    samplePrompts={SAMPLE_PROMPTS}
                />
            </div>
        </div>
    );
}
