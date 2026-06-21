/**
 * Self-Service Center — the config-driven quick-action catalog shown as an
 * alternate landing view to the Assistant chat. Categories + cards come from
 * `configuration.yaml › self_service_center` (served via /branding), so each
 * customer can curate the actions (and rebrand) without code changes.
 *
 * A card either seeds the Assistant with a `prompt` (the agent-first path) or
 * navigates to an in-app `route` (browse-type actions like Discover).
 */
import { Info } from 'lucide-react';
import { renderNavIcon } from '../../lib/navIcons';
import { useBrandingStore } from '../../stores/brandingStore';
import { useUserStore } from '../../stores/userStore';
import type {
    SelfServiceCenterCard,
    SelfServiceCenterCategory,
} from '../../services/api';

interface SelfServiceCenterProps {
    /** Seed the Assistant with a prompt and switch to it. */
    onLaunch: (prompt: string) => void;
    /** Navigate to an in-app route. */
    onNavigate: (route: string) => void;
}

export function SelfServiceCenter({ onLaunch, onNavigate }: SelfServiceCenterProps) {
    const selfServiceCenter = useBrandingStore((s) => s.selfServiceCenter);
    const currentPersona = useUserStore((s) => s.currentPersona);

    const categories = (selfServiceCenter.categories || []).filter(
        (c) => c && c.title && (c.cards || []).length > 0
    );

    // A card/category is visible when it has no persona gate or the current
    // persona is allowed. Keeps the catalog role-aware like the sidebar.
    const cardVisible = (card: SelfServiceCenterCard) =>
        !card.allowed_personas?.length ||
        card.allowed_personas.includes(currentPersona as string);

    const handleCard = (card: SelfServiceCenterCard) => {
        // route wins when both are set (documented in configuration.yaml).
        if (card.route) {
            onNavigate(card.route);
        } else if (card.prompt) {
            onLaunch(card.prompt);
        }
    };

    if (categories.length === 0) {
        return (
            <div className="max-w-5xl mx-auto w-full px-1 py-16 text-center text-gray-500">
                No self-service actions are configured yet.
            </div>
        );
    }

    return (
        <div className="max-w-5xl mx-auto w-full px-1 pb-10">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-8">
                {categories.map((category: SelfServiceCenterCategory) => {
                    const cards = (category.cards || []).filter(cardVisible);
                    if (cards.length === 0) return null;
                    return (
                        <div key={category.title} className="flex flex-col">
                            <div className="flex items-center gap-2 mb-3 text-primary">
                                {renderNavIcon(category.icon || 'Boxes', 'w-5 h-5')}
                                <h3 className="text-base font-bold">{category.title}</h3>
                            </div>
                            <div className="flex flex-col gap-2.5">
                                {cards.map((card) => (
                                    <button
                                        key={card.title}
                                        type="button"
                                        onClick={() => handleCard(card)}
                                        title={card.description || card.title}
                                        className="group flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-white px-4 py-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/5"
                                    >
                                        <span className="text-sm font-medium text-gray-900 truncate">
                                            {card.title}
                                        </span>
                                        <Info
                                            className="w-4 h-4 shrink-0 text-gray-300 group-hover:text-primary"
                                            aria-hidden="true"
                                        />
                                    </button>
                                ))}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
