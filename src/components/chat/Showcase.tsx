/**
 * `Showcase` — "See what's possible", shown under the empty chat box on the Home page.
 *
 * The chat box already has prompt suggestions tuned to each user, so a second grid of tiles under it
 * added nothing. This instead walks people through what the whole app can do for them, one short story
 * at a time: what you say or do, what happens next step by step, and what you end up with, plus a button
 * to try it. Stories play on their own until the user picks one (or pauses them), and stand still for
 * anyone who has asked their system for reduced motion.
 *
 * Only features a regular user has are covered, and each story is shown only while its feature is on.
 * Hiding it is kept per browser and per signed-in user (localStorage is shared by everyone on the browser).
 * While it shows, the Home page leaves out its prompt suggestions; hiding it brings them back.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
    ArrowRight,
    Award,
    BadgeCheck,
    BarChart,
    BookOpen,
    Calendar,
    CalendarClock,
    Check,
    CheckCheck,
    Clock,
    ExternalLink,
    FileText,
    Flag,
    GitFork,
    GraduationCap,
    KeyRound,
    ListChecks,
    Mail,
    MessageSquare,
    MousePointerClick,
    PackagePlus,
    Pause,
    Pin,
    Plane,
    Play,
    Search,
    Send,
    ShieldCheck,
    Sparkles,
    Table2,
    Trophy,
    Users,
    WandSparkles,
    X,
} from 'lucide-react';

import { useBrandingStore } from '../../stores/brandingStore';
import { GenieIcon } from '../icons/DatabricksIcons';
import {
    GUIDE_COMMUNITY,
    GUIDE_DISCOVER,
    GUIDE_MAKE_REQUEST,
    GUIDE_REPORT_PROBLEM,
    GUIDE_REQUESTS,
    GUIDE_TALK_TO_DATA,
    useGuideEnabled,
} from '../../lib/guide';

type Action =
    | { kind: 'ask'; prompt: string }
    | { kind: 'open'; label: string; to: string }
    | { kind: 'link'; label: string; href: string };

interface Story {
    id: string;
    icon: ReactNode;
    /** Short name on the story's tab. */
    tab: string;
    tease: string;
    headline: string;
    pitch: string;
    /** How it starts: something you say to the assistant, or something you do on a page. */
    trigger: { kind: 'say' | 'do'; text: string };
    steps: { icon: ReactNode; text: string }[];
    outcome: string;
    action: Action;
    guide?: string;
}

const TAB_ICON = 'w-4 h-4';
const STEP_ICON = 'w-4 h-4';

function buildStories(opts: {
    tabOn: (id: string) => boolean;
    requestsOn: boolean;
    genieOneUrl: string;
}): Story[] {
    const { tabOn, requestsOn, genieOneUrl } = opts;
    const stories: Story[] = [
        {
            id: 'access',
            icon: <KeyRound className={TAB_ICON} />,
            tab: 'Get access',
            tease: 'No more guessing who to ask',
            headline: 'Get access without chasing anyone',
            pitch: 'Say what you need in plain words. The assistant works out who can grant it and does the paperwork for you.',
            trigger: { kind: 'say', text: 'I need read access to the sales orders table.' },
            steps: [
                { icon: <Search className={STEP_ICON} />, text: 'Finds the table and checks whether your groups can already read it' },
                { icon: <Users className={STEP_ICON} />, text: 'Names the group that holds the access, so you can simply join it' },
                { icon: <ListChecks className={STEP_ICON} />, text: 'Spots an open request for the same thing, so nothing is asked twice' },
                { icon: <ShieldCheck className={STEP_ICON} />, text: 'Shows you the plan, and changes nothing until you confirm' },
                { icon: <Send className={STEP_ICON} />, text: 'Sends it to the right approvers' },
            ],
            outcome: requestsOn
                ? 'Your request is with the right people, and you can follow it under My Requests & Approvals.'
                : 'Your request is with the right people, with nothing left for you to chase.',
            action: { kind: 'ask', prompt: 'I need access to a table' },
            guide: GUIDE_MAKE_REQUEST,
        },
    ];

    if (tabOn('data_discovery')) {
        stories.push({
            id: 'discover',
            icon: <Search className={TAB_ICON} />,
            tab: 'Find trusted data',
            tease: 'Search the catalog like the web',
            headline: 'Find data you can actually trust',
            pitch: "Search with the words you'd use, not the table name. Every asset shows who looks after it and how healthy it is.",
            trigger: { kind: 'do', text: 'You search Discover for “shipments”' },
            steps: [
                { icon: <Search className={STEP_ICON} />, text: 'Matches any of your words, with certified data first' },
                { icon: <BadgeCheck className={STEP_ICON} />, text: 'Shows the owner, the quality checks and the service levels' },
                { icon: <GitFork className={STEP_ICON} />, text: 'Traces lineage: what feeds it and what depends on it' },
                { icon: <Table2 className={STEP_ICON} />, text: 'Previews real rows, read with your own permissions' },
                { icon: <Pin className={STEP_ICON} />, text: 'Pin it, share a link to it, or request access in one click' },
            ],
            outcome: 'The right table, who looks after it, and whether you can use it.',
            action: { kind: 'open', label: 'Open Discover', to: '/discovery' },
            guide: GUIDE_DISCOVER,
        });
    }

    if (genieOneUrl) {
        stories.push({
            id: 'talk_to_data',
            icon: <GenieIcon className={TAB_ICON} />,
            tab: 'Talk to Your Data',
            tease: 'Answers, charts and dashboards',
            headline: 'Ask your data a question, get an answer',
            pitch: 'Analysis happens in Talk to Your Data. Ask the assistant first and it gets you there with the right table and a question ready to go.',
            trigger: { kind: 'say', text: 'How many orders did we ship last month, by region?' },
            steps: [
                { icon: <Search className={STEP_ICON} />, text: 'Finds the full name of the table that holds the answer' },
                { icon: <WandSparkles className={STEP_ICON} />, text: 'Writes a question you can paste straight in' },
                { icon: <ExternalLink className={STEP_ICON} />, text: 'Links you to Talk to Your Data, across every workspace you can use' },
                { icon: <BarChart className={STEP_ICON} />, text: 'Ask in plain language and get answers, charts and dashboards' },
            ],
            outcome: 'Numbers and charts in minutes, without writing a line of SQL.',
            action: { kind: 'link', label: 'Open Talk to Your Data', href: genieOneUrl },
            guide: GUIDE_TALK_TO_DATA,
        });
    }

    stories.push({
        id: 'set_up',
        icon: <PackagePlus className={TAB_ICON} />,
        tab: 'Set something up',
        tease: 'Schemas, volumes, workspaces and more',
        headline: 'Stand up something new, the governed way',
        pitch: 'Ask for a schema, volume, workspace, repo or service principal. The assistant makes sure it fits, gathers the details and gets it signed off.',
        trigger: { kind: 'say', text: "I want a new schema for my team's forecasting work." },
        steps: [
            { icon: <Search className={STEP_ICON} />, text: "Checks what already exists, so you don't build a duplicate" },
            { icon: <MessageSquare className={STEP_ICON} />, text: 'Asks only for the details it still needs' },
            { icon: <ShieldCheck className={STEP_ICON} />, text: 'Shows you the plan, and changes nothing until you confirm' },
            { icon: <CheckCheck className={STEP_ICON} />, text: "Gets sign-off where it's needed, then it's set up" },
        ],
        outcome: 'Built the way your organization expects, and tracked from request to done.',
        action: { kind: 'ask', prompt: 'I want to set up a new schema' },
        guide: GUIDE_MAKE_REQUEST,
    });

    if (requestsOn) {
        stories.push({
            id: 'track',
            icon: <ListChecks className={TAB_ICON} />,
            tab: 'Track and approve',
            tease: 'See who every request waits on',
            headline: 'Always know where things stand',
            pitch: 'The requests you made and the approvals waiting on you live on one page, so nothing gets lost in an inbox.',
            trigger: { kind: 'do', text: 'You open My Requests & Approvals' },
            steps: [
                { icon: <Clock className={STEP_ICON} />, text: "See each request's status, and exactly who it's waiting on" },
                { icon: <FileText className={STEP_ICON} />, text: 'Read what earlier steps found before you decide' },
                { icon: <CheckCheck className={STEP_ICON} />, text: 'Approve or reject from one list, several at once' },
                { icon: <Plane className={STEP_ICON} />, text: "Going away? Hand your approvals to a colleague until you're back" },
            ],
            outcome: 'No more emails asking “any update on this?”.',
            action: { kind: 'open', label: 'Open My Requests & Approvals', to: '/requests' },
            guide: GUIDE_REQUESTS,
        });
    }

    if (tabOn('reports')) {
        stories.push({
            id: 'reports',
            icon: <CalendarClock className={TAB_ICON} />,
            tab: 'Reports on autopilot',
            tease: 'Your questions, answered on a schedule',
            headline: 'Put your recurring questions on a schedule',
            pitch: 'Write down the questions you ask every week, once. The agent answers them on schedule and emails the report.',
            trigger: { kind: 'do', text: 'You set up a report on the Reports page' },
            steps: [
                { icon: <MessageSquare className={STEP_ICON} />, text: 'Write the questions you want answered' },
                { icon: <Calendar className={STEP_ICON} />, text: 'Pick when it runs, from every morning to once a month' },
                { icon: <Users className={STEP_ICON} />, text: 'List who should get it' },
                { icon: <Mail className={STEP_ICON} />, text: 'The agent runs it and emails the report to everyone on the list' },
            ],
            outcome: 'Fresh answers in your inbox, without lifting a finger.',
            action: { kind: 'open', label: 'Open Reports', to: '/reports' },
        });
    }

    const learnSteps: Story['steps'] = [];
    if (tabOn('training')) {
        learnSteps.push(
            { icon: <GraduationCap className={STEP_ICON} />, text: 'Follow learning tracks and see your progress as you go' },
            { icon: <Award className={STEP_ICON} />, text: 'Earn certifications, and unlock access some data needs' },
        );
    }
    if (tabOn('event_calendar')) {
        learnSteps.push({ icon: <Calendar className={STEP_ICON} />, text: 'Join workshops and office hours, and add them to your calendar' });
    }
    if (tabOn('templates_assets')) {
        learnSteps.push({ icon: <FileText className={STEP_ICON} />, text: 'Reuse templates and patterns other teams have published' });
    }
    if (learnSteps.length > 0) {
        stories.push({
            id: 'learn',
            icon: <GraduationCap className={TAB_ICON} />,
            tab: 'Level up',
            tease: 'Courses, events and templates',
            headline: "Learn from what's already worked here",
            pitch: 'Ask the assistant where to start and it points you to the courses, sessions and resources your organization recommends.',
            trigger: { kind: 'say', text: 'What should I learn first for my role?' },
            steps: learnSteps,
            outcome: 'Up to speed faster, building on work other teams have already done.',
            action: { kind: 'ask', prompt: 'What should I learn first for my role?' },
            guide: GUIDE_COMMUNITY,
        });
    }

    if (tabOn('feedback')) {
        stories.push({
            id: 'report_problem',
            icon: <Flag className={TAB_ICON} />,
            tab: 'Flag bad data',
            tease: 'Straight to the people who can fix it',
            headline: 'Spot a problem? Get it to the owner',
            pitch: "Tell the assistant what looks wrong. It finds who looks after the data and reports it for you, so you don't have to track them down.",
            trigger: { kind: 'say', text: 'The shipments table looks like it stopped updating.' },
            steps: [
                { icon: <Search className={STEP_ICON} />, text: 'Looks up who owns the data' },
                { icon: <FileText className={STEP_ICON} />, text: 'Drafts the report and shows it to you first' },
                { icon: <Send className={STEP_ICON} />, text: 'Sends it to the team once you say yes' },
            ],
            outcome: 'The right team hears about it, and you never had to find them.',
            action: { kind: 'ask', prompt: 'I want to report a problem with some data' },
            guide: GUIDE_REPORT_PROBLEM,
        });
    }

    return stories;
}

// Timing for one story: type what you say, then each step rises in, then the result, then a pause to read.
const TYPE_MS = 28;
const AFTER_TRIGGER_MS = 350;
const STEP_GAP_MS = 650;
const DWELL_MS = 4500;

function timeline(story: Story, animate: boolean) {
    if (!animate) return { stepDelay: () => 0, outcomeDelay: 0, duration: 0 };
    const typing = story.trigger.kind === 'say' ? story.trigger.text.length * TYPE_MS : 0;
    const stepDelay = (i: number) => typing + AFTER_TRIGGER_MS + i * STEP_GAP_MS;
    const outcomeDelay = stepDelay(story.steps.length);
    return { stepDelay, outcomeDelay, duration: outcomeDelay + DWELL_MS };
}

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

function usePrefersReducedMotion(): boolean {
    return useSyncExternalStore(
        (onChange) => {
            const mq = window.matchMedia?.(REDUCED_MOTION);
            mq?.addEventListener('change', onChange);
            return () => mq?.removeEventListener('change', onChange);
        },
        () => window.matchMedia?.(REDUCED_MOTION).matches ?? false,
    );
}

function usePageHidden(): boolean {
    return useSyncExternalStore(
        (onChange) => {
            document.addEventListener('visibilitychange', onChange);
            return () => document.removeEventListener('visibilitychange', onChange);
        },
        () => document.hidden,
    );
}

/** Types `text` out a character at a time. Remount it (via `key`) to start again. */
function Typewriter({ text, animate }: { text: string; animate: boolean }) {
    const [shown, setShown] = useState(animate ? 0 : text.length);
    const done = shown >= text.length;
    useEffect(() => {
        if (!animate || done) return;
        const id = window.setInterval(() => setShown((n) => Math.min(n + 1, text.length)), TYPE_MS);
        return () => window.clearInterval(id);
    }, [animate, done, text.length]);
    return (
        <>
            {/* The full text for screen readers; the typing is decoration. */}
            <span className="sr-only">{text}</span>
            <span aria-hidden="true">
                {text.slice(0, shown)}
                {!done && <span className="showcase-caret ml-px inline-block h-[1.05em] w-[2px] translate-y-[3px] bg-white/80" />}
            </span>
        </>
    );
}

interface ShowcaseProps {
    /** Signed-in user's email; nothing shows until it's known, since whether they hid it isn't. */
    user: string;
    /** From `useShowcaseHidden` (`hooks/useShowcaseHidden.ts`). */
    hidden: boolean;
    onHiddenChange: (hidden: boolean) => void;
    /** Start a conversation with this message. */
    onAsk: (prompt: string) => void;
    onOpen: (path: string) => void;
}

export function Showcase({ user, hidden, onHiddenChange, onAsk, onOpen }: ShowcaseProps) {
    const genieOneUrl = useBrandingStore((s) => s.genieOneUrl);
    const uiTabs = useBrandingStore((s) => s.uiTabs);
    const guideOn = useGuideEnabled();
    const reducedMotion = usePrefersReducedMotion();
    const pageHidden = usePageHidden();

    const stories = useMemo(
        () =>
            buildStories({
                tabOn: (id) => uiTabs?.[id] !== false,
                requestsOn: uiTabs?.my_requests !== false || uiTabs?.pending_approvals !== false,
                genieOneUrl,
            }),
        [uiTabs, genieOneUrl],
    );

    const [activeId, setActiveId] = useState<string | null>(null);
    // Stories play on their own until the user picks one or presses pause.
    const [autoplay, setAutoplay] = useState(true);
    const [hovering, setHovering] = useState(false);
    const [focused, setFocused] = useState(false);
    const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
    const tabListRef = useRef<HTMLDivElement | null>(null);

    const activeIndex = Math.max(0, stories.findIndex((s) => s.id === activeId));
    const story = stories[activeIndex];
    const animate = !reducedMotion;
    const playing = animate && autoplay && stories.length > 1;
    const paused = hovering || focused || pageHidden;
    const { stepDelay, outcomeDelay, duration } = timeline(story, animate);

    // On narrow screens the tabs are a sideways-scrolling row: keep the playing story's tab in view.
    // Scrolls only the row, never the page.
    const storyId = story?.id;
    useEffect(() => {
        const list = tabListRef.current;
        const tab = storyId ? tabRefs.current[storyId] : null;
        if (!list || !tab || list.scrollWidth <= list.clientWidth) return;
        list.scrollTo({ left: Math.max(0, tab.offsetLeft - 4), behavior: animate ? 'smooth' : 'auto' });
    }, [storyId, animate]);

    const goTo = (index: number, opts: { manual: boolean; focus?: boolean }) => {
        const next = stories[(index + stories.length) % stories.length];
        setActiveId(next.id);
        if (opts.manual) setAutoplay(false);
        if (opts.focus) tabRefs.current[next.id]?.focus();
    };

    const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
        const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (step) {
            e.preventDefault();
            goTo(activeIndex + step, { manual: true, focus: true });
        } else if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();
            goTo(e.key === 'Home' ? 0 : stories.length - 1, { manual: true, focus: true });
        }
    };

    const runAction = () => {
        const a = story.action;
        if (a.kind === 'ask') onAsk(a.prompt);
        else if (a.kind === 'open') onOpen(a.to);
        else window.open(a.href, '_blank', 'noopener,noreferrer');
    };

    // Until we know who is signed in we can't know whether they hid it; showing it then hiding it would flash.
    if (!user || !story) return null;

    if (hidden) {
        return (
            <div className="max-w-5xl mx-auto w-full px-1 pb-6 text-center">
                <button
                    type="button"
                    onClick={() => onHiddenChange(false)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-400 hover:text-primary transition-colors"
                >
                    <Sparkles className="w-3.5 h-3.5" />
                    See what's possible
                </button>
            </div>
        );
    }

    const actionLabel = story.action.kind === 'ask' ? 'Try it now' : story.action.label;

    return (
        <section aria-labelledby="showcase-heading" className="max-w-5xl mx-auto w-full px-1 pb-8">
            <div
                className="relative overflow-hidden rounded-3xl bg-nav text-white shadow-xl"
                onMouseEnter={() => setHovering(true)}
                onMouseLeave={() => setHovering(false)}
                onFocus={() => setFocused(true)}
                onBlur={(e) => {
                    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
                }}
            >
                {/* Backdrop: two soft brand-colored glows over a faint dot grid. */}
                <div aria-hidden="true" className="pointer-events-none absolute -top-28 -right-20 h-80 w-80 rounded-full bg-primary/30 blur-3xl" />
                <div
                    aria-hidden="true"
                    className="pointer-events-none absolute -bottom-36 -left-24 h-96 w-96 rounded-full blur-3xl"
                    style={{ background: 'color-mix(in srgb, var(--theme-accent) 28%, transparent)' }}
                />
                <div
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-0 opacity-[0.07]"
                    style={{ backgroundImage: 'radial-gradient(white 1px, transparent 1px)', backgroundSize: '18px 18px' }}
                />

                <div className="relative p-5 sm:p-7">
                    <header className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                            <p className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-white/80 ring-1 ring-white/15">
                                <Sparkles className="w-3.5 h-3.5 text-primary" />
                                See what's possible
                            </p>
                            <h3 id="showcase-heading" className="mt-3 text-2xl sm:text-[28px] font-bold leading-tight tracking-tight">
                                Less chasing. More doing.
                            </h3>
                            <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-white/65">
                                A tour of what you can get done here, from getting access to putting reports on
                                autopilot. Pick a story to watch it play out, then try it yourself.
                            </p>
                        </div>
                        <div className="flex items-center gap-1">
                            {animate && stories.length > 1 && (
                                <button
                                    type="button"
                                    onClick={() => setAutoplay((v) => !v)}
                                    className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-white/60 hover:bg-white/10 hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                                >
                                    {autoplay ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                                    {autoplay ? 'Pause' : 'Play'}
                                </button>
                            )}
                            <button
                                type="button"
                                onClick={() => onHiddenChange(true)}
                                aria-label="Hide what's possible"
                                className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-xs font-medium text-white/60 hover:bg-white/10 hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                            >
                                Hide <X className="w-3.5 h-3.5" />
                            </button>
                        </div>
                    </header>

                    <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)] lg:gap-5">
                        {/* Story tabs: a row that scrolls sideways on narrow screens, a column on wide ones. */}
                        <div
                            ref={tabListRef}
                            role="tablist"
                            aria-label="Stories"
                            className="hide-scrollbar relative -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0"
                        >
                            {stories.map((s, i) => {
                                const active = i === activeIndex;
                                return (
                                    <button
                                        key={s.id}
                                        ref={(el) => {
                                            tabRefs.current[s.id] = el;
                                        }}
                                        type="button"
                                        role="tab"
                                        id={`showcase-tab-${s.id}`}
                                        aria-selected={active}
                                        aria-controls="showcase-panel"
                                        tabIndex={active ? 0 : -1}
                                        onClick={() => goTo(i, { manual: true })}
                                        onKeyDown={onTabKeyDown}
                                        className={`group relative flex w-64 shrink-0 items-center gap-3 overflow-hidden rounded-xl px-3 py-2.5 text-left transition-colors lg:w-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 ${
                                            active ? 'bg-white/15 ring-1 ring-white/20' : 'hover:bg-white/5'
                                        }`}
                                    >
                                        <span
                                            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors ${
                                                active
                                                    ? 'bg-primary text-white shadow-lg shadow-primary/30'
                                                    : 'bg-white/10 text-white/70 group-hover:text-white'
                                            }`}
                                        >
                                            {s.icon}
                                        </span>
                                        <span className="min-w-0">
                                            <span className={`block text-sm font-semibold ${active ? 'text-white' : 'text-white/80'}`}>
                                                {s.tab}
                                            </span>
                                            <span className="mt-0.5 block text-xs leading-snug text-white/50">{s.tease}</span>
                                        </span>
                                        {active && playing && (
                                            <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-0.5 bg-white/10">
                                                <span
                                                    className="showcase-progress block h-full bg-primary"
                                                    style={{
                                                        animationDuration: `${duration}ms`,
                                                        animationPlayState: paused ? 'paused' : 'running',
                                                    }}
                                                    onAnimationEnd={() => goTo(activeIndex + 1, { manual: false })}
                                                />
                                            </span>
                                        )}
                                    </button>
                                );
                            })}
                        </div>

                        {/* The story. Keyed so switching stories replays it from the start. */}
                        <div
                            id="showcase-panel"
                            role="tabpanel"
                            aria-labelledby={`showcase-tab-${story.id}`}
                            aria-live={playing ? 'off' : 'polite'}
                            className="rounded-2xl bg-white text-gray-900 shadow-2xl ring-1 ring-black/5"
                        >
                            <div key={story.id} className="flex h-full flex-col p-5 sm:p-6">
                                <h4 className="showcase-rise text-xl font-bold leading-snug text-heading">{story.headline}</h4>
                                <p className="showcase-rise mt-1 text-sm leading-relaxed text-gray-600" style={{ animationDelay: '60ms' }}>
                                    {story.pitch}
                                </p>

                                <div className="mt-5 flex-1 rounded-xl border border-gray-100 bg-surface p-4 sm:p-5">
                                    {story.trigger.kind === 'say' ? (
                                        <div className="flex justify-end">
                                            <div className="max-w-[85%]">
                                                <p className="mb-1 text-right text-[11px] font-semibold uppercase tracking-wider text-gray-400">You say</p>
                                                <p className="rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm font-medium text-white shadow-sm">
                                                    <Typewriter text={story.trigger.text} animate={animate} />
                                                </p>
                                            </div>
                                        </div>
                                    ) : (
                                        <p className="showcase-rise inline-flex items-center gap-2 rounded-xl bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-200">
                                            <MousePointerClick className="w-4 h-4 shrink-0 text-primary" />
                                            {story.trigger.text}
                                        </p>
                                    )}

                                    <p
                                        className="showcase-rise mt-4 text-[11px] font-semibold uppercase tracking-wider text-gray-400"
                                        style={{ animationDelay: `${Math.max(0, stepDelay(0) - 150)}ms` }}
                                    >
                                        What happens next
                                    </p>
                                    <ol className="mt-2.5 space-y-2.5">
                                        {story.steps.map((st, i) => (
                                            <li
                                                key={st.text}
                                                className="showcase-rise relative flex items-start gap-3"
                                                style={{ animationDelay: `${stepDelay(i)}ms` }}
                                            >
                                                {i < story.steps.length - 1 && (
                                                    <span aria-hidden="true" className="absolute left-4 top-8 -bottom-2.5 w-px bg-gray-200" />
                                                )}
                                                <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white text-primary shadow-sm ring-1 ring-gray-200">
                                                    {st.icon}
                                                    <span
                                                        aria-hidden="true"
                                                        className="showcase-pop absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-success text-white ring-2 ring-white"
                                                        style={{ animationDelay: `${stepDelay(i) + (animate ? 380 : 0)}ms` }}
                                                    >
                                                        <Check className="h-2.5 w-2.5" strokeWidth={3.5} />
                                                    </span>
                                                </span>
                                                <span className="pt-1.5 text-sm leading-snug text-gray-700">{st.text}</span>
                                            </li>
                                        ))}
                                    </ol>

                                    <div
                                        className="showcase-rise mt-4 flex items-start gap-3 rounded-xl bg-success/10 px-3.5 py-3 ring-1 ring-success/20"
                                        style={{ animationDelay: `${outcomeDelay}ms` }}
                                    >
                                        <Trophy className="mt-0.5 w-4 h-4 shrink-0 text-success" />
                                        <div>
                                            <p className="text-[11px] font-bold uppercase tracking-wider text-success">The result</p>
                                            <p className="mt-0.5 text-sm font-medium text-gray-900">{story.outcome}</p>
                                        </div>
                                    </div>
                                </div>

                                <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
                                    <button
                                        type="button"
                                        onClick={runAction}
                                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-md shadow-primary/20 transition-all hover:shadow-lg hover:shadow-primary/30 hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2"
                                    >
                                        {actionLabel}
                                        {story.action.kind === 'link' ? <ExternalLink className="w-4 h-4" /> : <ArrowRight className="w-4 h-4" />}
                                    </button>
                                    {story.action.kind === 'ask' && (
                                        <span className="text-xs text-gray-500">
                                            Starts a chat with “{story.action.prompt}”
                                        </span>
                                    )}
                                    {guideOn && story.guide && (
                                        <Link
                                            to={story.guide}
                                            className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-600 hover:text-primary transition-colors"
                                        >
                                            <BookOpen className="w-4 h-4" />
                                            How it works
                                        </Link>
                                    )}
                                    {stories.length > 1 && (
                                        <button
                                            type="button"
                                            onClick={() => goTo(activeIndex + 1, { manual: true })}
                                            className="ml-auto inline-flex items-center gap-1 text-sm font-medium text-gray-500 hover:text-heading transition-colors"
                                        >
                                            Next story <ArrowRight className="w-4 h-4" />
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}
