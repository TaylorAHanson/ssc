/**
 * `AgentWelcome` — the shared empty-state header for every agent chat
 * surface (Self Service / Governance / FinOps on Home, and Ask Your
 * Data). It deliberately shows a SINGLE, non-clickable example rather
 * than a grid/list of canned prompts: a long list of clickable options
 * reads as "this is all the agent can do", whereas one emphasized
 * example just hints at the shape of a good question and leaves the
 * input open-ended.
 *
 * Keep both surfaces routed through this component so their empty
 * states stay 100% identical.
 */
import { Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';

export interface AgentWelcomeExample {
    /** Short category label, e.g. "Data" or "Requests". */
    label: string;
    /** The example question (no surrounding quotes — added here). */
    text: string;
}

export interface AgentWelcomeProps {
    /** Short title, e.g. the agent / page name. */
    title: string;
    /** One-line description of what the agent does. Omit to reclaim space. */
    description?: string;
    /**
     * Optional example questions, rendered as non-clickable emphasized
     * lines. Each hints at a capability bucket via its `label`. Omit when a
     * surface shows its own (e.g. personalized) starting prompts instead.
     */
    examples?: AgentWelcomeExample[];
    /** Optional icon; defaults to the Sparkles glyph. */
    icon?: ReactNode;
}

export function AgentWelcome({ title, description, examples = [], icon }: AgentWelcomeProps) {
    return (
        <div className="flex flex-col items-center justify-center py-10 gap-4 text-center">
            <div className="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center">
                {icon ?? <Sparkles className="w-7 h-7 text-primary" />}
            </div>
            <div className="max-w-md">
                <h2 className="text-xl font-semibold text-gray-900">{title}</h2>
                {description && <p className="text-sm text-gray-500 mt-1">{description}</p>}
            </div>
            {examples.length > 0 && (
                <div className="flex flex-col gap-1.5 max-w-md">
                    <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                        For example, try asking
                    </p>
                    {examples.map((ex) => (
                        <p key={ex.label} className="text-sm text-gray-600">
                            <span className="font-semibold text-gray-700">{ex.label}:</span>{' '}
                            <span className="italic text-gray-900">“{ex.text}”</span>
                        </p>
                    ))}
                </div>
            )}
        </div>
    );
}
