import React, { useState, useRef, useEffect } from 'react';
import { LayoutGrid, MonitorDot, Headphones, ExternalLink, FileCode } from 'lucide-react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// App registry — add new apps here in the future
// ---------------------------------------------------------------------------
interface AppEntry {
    id: string;
    name: string;
    description: string;
    /** Lucide icon component */
    Icon: React.ElementType;
    /** URL to open — null means "this app" (current) */
    href: string | null;
    /** Tailwind / hex color accent for the icon background */
    accentColor: string;
    accentBg: string;
}

const APPS: AppEntry[] = [
    {
        id: 'command-center',
        name: 'Command Center',
        description: 'Dashboards & widgets',
        Icon: MonitorDot,
        href: null, // current app
        accentColor: '#007BFF',
        accentBg: '#EBF4FF',
    },
    {
        id: 'self-service',
        name: 'Self Service Center',
        description: 'Self Service Center',
        Icon: Headphones,
        href: 'https://qualcomm.com/',
        accentColor: '#00875A',
        accentBg: '#E3F9F0',
    },
    {
        id: 'third-app',
        name: 'Third App',
        description: 'Third App',
        Icon: FileCode,
        href: 'https://qualcomm.com/',
        accentColor: '#00875A',
        accentBg: '#E3F9F0',
    },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export const AppSwitcher: React.FC = () => {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    // Close on outside click
    useEffect(() => {
        if (!isOpen) return;

        const handler = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [isOpen]);

    // Close on Escape
    useEffect(() => {
        if (!isOpen) return;

        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setIsOpen(false);
        };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [isOpen]);

    return (
        <div ref={containerRef} className="relative">
            {/* Trigger button */}
            <button
                onClick={() => setIsOpen((prev) => !prev)}
                aria-label="Switch app"
                aria-expanded={isOpen}
                className={clsx(
                    'p-2 rounded-full transition-all duration-150',
                    isOpen
                        ? 'bg-gray-100 text-qualcomm-navy'
                        : 'text-gray-500 hover:bg-gray-100 hover:text-qualcomm-navy',
                )}
            >
                <LayoutGrid className="w-5 h-5" />
            </button>

            {/* Dropdown panel */}
            {isOpen && (
                <div
                    className={clsx(
                        'absolute right-0 top-full mt-3 z-50',
                        'w-72 bg-white rounded-2xl shadow-2xl border border-gray-100',
                        'animate-in fade-in zoom-in-95 duration-150',
                    )}
                    style={{ boxShadow: '0 8px 30px rgba(0,0,0,0.12)' }}
                >
                    {/* Header */}
                    <div className="px-4 pt-4 pb-2">
                        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                            Switch app
                        </p>
                    </div>

                    {/* App grid */}
                    <div className="grid grid-cols-2 gap-1 p-3">
                        {APPS.map((app) => {
                            const isCurrent = app.href === null;
                            const { Icon } = app;

                            const content = (
                                <div
                                    className={clsx(
                                        'flex flex-col items-center gap-2.5 p-4 rounded-xl transition-all duration-150 group relative',
                                        isCurrent
                                            ? 'bg-blue-50 cursor-default ring-1 ring-qualcomm-blue/20'
                                            : 'hover:bg-gray-50 cursor-pointer',
                                    )}
                                >
                                    {/* Icon */}
                                    <div
                                        className="w-12 h-12 rounded-2xl flex items-center justify-center shadow-sm transition-transform duration-150 group-hover:scale-105"
                                        style={{ backgroundColor: app.accentBg }}
                                    >
                                        <Icon
                                            className="w-6 h-6"
                                            style={{ color: app.accentColor }}
                                        />
                                    </div>

                                    {/* Name + desc */}
                                    <div className="text-center">
                                        <p
                                            className={clsx(
                                                'text-sm font-semibold leading-tight',
                                                isCurrent ? 'text-qualcomm-navy' : 'text-gray-700',
                                            )}
                                        >
                                            {app.name}
                                        </p>
                                        <p className="text-xs text-gray-400 mt-0.5 leading-tight">
                                            {app.description}
                                        </p>
                                    </div>

                                    {/* "Current" badge */}
                                    {isCurrent && (
                                        <span className="absolute top-2 right-2 text-[10px] font-semibold text-qualcomm-blue bg-white rounded-full px-1.5 py-0.5 border border-qualcomm-blue/20 leading-none">
                                            Current
                                        </span>
                                    )}

                                    {/* External link indicator */}
                                    {!isCurrent && (
                                        <ExternalLink className="absolute top-2 right-2 w-3 h-3 text-gray-300 group-hover:text-gray-400 transition-colors" />
                                    )}
                                </div>
                            );

                            return isCurrent ? (
                                <div key={app.id}>{content}</div>
                            ) : (
                                <a
                                    key={app.id}
                                    href={app.href!}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={() => setIsOpen(false)}
                                >
                                    {content}
                                </a>
                            );
                        })}
                    </div>

                    {/* Footer */}
                    <div className="px-4 pb-3 pt-1 border-t border-gray-50">
                        <p className="text-[11px] text-gray-400 text-center">
                            Qualcomm Internal Tools
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
};
