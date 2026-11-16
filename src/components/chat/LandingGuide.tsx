/**
 * `LandingGuide` — what the assistant can do, shown under the empty chat box on the Home page.
 *
 * An empty input is open-ended, so people either ask for things the assistant isn't for (analyzing
 * data) or don't know what it can take off their hands. This lays out the kinds of things it does as a
 * few quiet tiles. A tile either starts a conversation with an example or opens the page that covers it.
 *
 * It can be hidden; that choice is kept per browser and per signed-in user (localStorage is shared by
 * everyone who uses the browser). A small link brings it back.
 */
import { useState, type ReactNode } from 'react';
import { ChevronRight, Flag, GraduationCap, KeyRound, ListChecks, PackagePlus, Search, X } from 'lucide-react';

interface Capability {
    id: string;
    icon: ReactNode;
    title: string;
    description: string;
    /** Starts a conversation with this message. */
    prompt?: string;
    /** Opens this page instead. */
    to?: string;
}

const ICON = 'w-4 h-4';

function capabilities(opts: { trainingOn: boolean; requestsOn: boolean }): Capability[] {
    const items: Capability[] = [
        {
            id: 'access',
            icon: <KeyRound className={ICON} />,
            title: 'Get access',
            description: 'To a table, schema, dashboard or group.',
            prompt: 'I need access to a table',
        },
        {
            id: 'create',
            icon: <PackagePlus className={ICON} />,
            title: 'Set something up',
            description: 'A schema, volume, workspace, repo or service principal.',
            prompt: 'I want to set up a new schema',
        },
        ...(opts.requestsOn
            ? [{
                id: 'track',
                icon: <ListChecks className={ICON} />,
                title: 'Track your requests',
                description: 'See where one stands, and approve what is waiting on you.',
                to: '/requests',
            }]
            : []),
        {
            id: 'find',
            icon: <Search className={ICON} />,
            title: 'Find data and its owner',
            description: 'Locate a table and who can say yes to access.',
            prompt: 'Who owns the orders table?',
        },
        {
            id: 'report',
            icon: <Flag className={ICON} />,
            title: 'Report a problem',
            description: 'Stale or wrong data, or something here that is not working.',
            prompt: 'I want to report a problem',
        },
    ];
    if (opts.trainingOn) {
        items.push({
            id: 'learn',
            icon: <GraduationCap className={ICON} />,
            title: 'Learn the platform',
            description: 'Courses, events and templates.',
            to: '/community/training',
        });
    }
    return items;
}

const storageKey = (user: string) => `landing_guide_hidden:${user}`;

function readHidden(user: string): boolean {
    try {
        return window.localStorage.getItem(storageKey(user)) === '1';
    } catch {
        return false;
    }
}

interface LandingGuideProps {
    /** Signed-in user's email, so the "hidden" choice isn't shared across accounts on one browser. */
    user: string;
    trainingOn: boolean;
    /** Whether the Requests page is on for this deployment. */
    requestsOn: boolean;
    /** Start a conversation with this message. */
    onAsk: (prompt: string) => void;
    onOpen: (path: string) => void;
}

export function LandingGuide({ user, trainingOn, requestsOn, onAsk, onOpen }: LandingGuideProps) {
    // What was clicked this session, for this user. Otherwise the saved choice is read for `user`, which
    // matters because the user isn't known on the first render after a reload.
    const [clicked, setClicked] = useState<{ user: string; hidden: boolean } | null>(null);
    const hidden = clicked?.user === user ? clicked.hidden : readHidden(user);

    const setHiddenPersisted = (value: boolean) => {
        setClicked({ user, hidden: value });
        try {
            if (value) window.localStorage.setItem(storageKey(user), '1');
            else window.localStorage.removeItem(storageKey(user));
        } catch {
            /* storage disabled: the choice just lasts until reload */
        }
    };

    // Until we know who is signed in we can't know whether they hid it; showing it then hiding it would flash.
    if (!user) return null;

    if (hidden) {
        return (
            <div className="max-w-3xl mx-auto w-full px-1 pb-4 text-center">
                <button
                    type="button"
                    onClick={() => setHiddenPersisted(false)}
                    className="text-xs font-medium text-gray-400 hover:text-primary transition-colors"
                >
                    What can I help with?
                </button>
            </div>
        );
    }

    return (
        <section aria-label="What I can help with" className="max-w-3xl mx-auto w-full px-1 pb-6">
            <div className="flex items-center justify-between mb-2.5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">What I can help with</h3>
                <button
                    type="button"
                    onClick={() => setHiddenPersisted(true)}
                    className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-gray-700 transition-colors"
                    aria-label="Hide this list"
                >
                    Hide <X className="w-3 h-3" />
                </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                {capabilities({ trainingOn, requestsOn }).map((c) => (
                    <button
                        key={c.id}
                        type="button"
                        onClick={() => (c.to ? onOpen(c.to) : c.prompt && onAsk(c.prompt))}
                        className="group flex items-start gap-3 rounded-xl border border-gray-200 bg-white/60 p-3 text-left transition-colors hover:border-primary/50 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                    >
                        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                            {c.icon}
                        </span>
                        <span className="min-w-0 flex-1">
                            <span className="block text-[13px] font-semibold text-gray-900 group-hover:text-primary transition-colors">
                                {c.title}
                            </span>
                            <span className="mt-0.5 block text-xs leading-snug text-gray-500">{c.description}</span>
                        </span>
                        {c.to && <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-gray-300 group-hover:text-primary" />}
                    </button>
                ))}
            </div>
        </section>
    );
}
