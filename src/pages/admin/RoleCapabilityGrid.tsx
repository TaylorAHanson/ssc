import { Fragment, useEffect, useState } from 'react';
import { ArrowRight, Check, Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import type { CapabilityGrid } from '../../types';
import { userService } from '../../services/userService';
import { errorMessage } from '../../lib/errors';

/** The Role Permissions settings page: loads the grid and links back to Role Mappings. */
export const RolePermissions = ({ onOpenMappings }: { onOpenMappings: () => void }) => {
    const [grid, setGrid] = useState<CapabilityGrid | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        userService
            .getCapabilityGrid()
            .then(setGrid)
            .catch((err) => setError(errorMessage(err, 'Failed to load role capabilities')));
    }, []);

    if (error) {
        return (
            <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
                Couldn't load what each role can do: {error}
            </div>
        );
    }
    if (!grid) {
        return (
            <div className="flex justify-center items-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }
    return <RoleCapabilityGrid grid={grid} onOpenMappings={onOpenMappings} />;
};

/** Read-only "What each role can do" table, built from GET /roles/capabilities. */
export const RoleCapabilityGrid = ({ grid, onOpenMappings }: { grid: CapabilityGrid; onOpenMappings?: () => void }) => {
    const groups = grid.groups.filter((g) => grid.capabilities.some((c) => c.group === g));

    // One footnote per setting that adds roles to a capability, numbered in table order.
    const footnotes: { key: string; label: string; current: string[] }[] = [];
    for (const group of groups) {
        for (const cap of grid.capabilities) {
            const extra = cap.also_from_setting;
            if (cap.group === group && extra && !footnotes.some((f) => f.key === extra.key)) {
                footnotes.push({ key: extra.key, label: extra.label, current: extra.current_roles });
            }
        }
    }
    const footnoteFor = (key: string) => footnotes.findIndex((f) => f.key === key) + 1;

    return (
        <Card>
            <CardHeader>
                <CardTitle>What each role can do</CardTitle>
                <p className="text-sm text-gray-500">
                    Read-only. Super Admin holds every permission. A person with several roles gets everything
                    each of them allows.
                </p>
                {onOpenMappings && (
                    <button
                        type="button"
                        onClick={onOpenMappings}
                        className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline self-start"
                    >
                        Change who holds a role in Role Mappings <ArrowRight className="w-3.5 h-3.5" />
                    </button>
                )}
            </CardHeader>
            <CardContent>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                        <thead className="text-xs text-gray-700 bg-gray-50 border-b">
                            <tr>
                                <th className="px-4 py-3 font-medium min-w-[260px]">Permission</th>
                                {grid.roles.map((role) => (
                                    <th
                                        key={role.key}
                                        className="px-2 py-3 font-medium text-center whitespace-nowrap"
                                        title={role.description}
                                    >
                                        {role.label}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {groups.map((group) => (
                                <Fragment key={group}>
                                    <tr className="bg-gray-50/60 border-b">
                                        <td
                                            colSpan={grid.roles.length + 1}
                                            className="px-4 py-2 text-xs font-bold uppercase tracking-wider text-gray-500"
                                        >
                                            {group}
                                        </td>
                                    </tr>
                                    {grid.capabilities.filter((c) => c.group === group).map((cap) => {
                                        const extra = cap.also_from_setting;
                                        const mark = extra ? footnoteFor(extra.key) : null;
                                        return (
                                            <tr key={cap.id} className="bg-white border-b">
                                                <td className="px-4 py-3">
                                                    <div className="font-medium text-gray-900">
                                                        {cap.label}
                                                        {mark !== null && <sup className="ml-0.5 text-primary">{mark}</sup>}
                                                    </div>
                                                    <div className="text-xs text-gray-500">{cap.description}</div>
                                                    {cap.note && <div className="text-xs text-gray-400 mt-0.5">{cap.note}</div>}
                                                </td>
                                                {grid.roles.map((role) => {
                                                    const holds = cap.roles.includes(role.label);
                                                    return (
                                                        <td key={role.key} className="px-2 py-3 text-center">
                                                            {holds ? (
                                                                <Check
                                                                    className="w-4 h-4 text-success inline"
                                                                    aria-label={`${role.label} can ${cap.label.toLowerCase()}`}
                                                                />
                                                            ) : (
                                                                <span className="text-gray-300" aria-hidden>·</span>
                                                            )}
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        );
                                    })}
                                </Fragment>
                            ))}
                        </tbody>
                    </table>
                </div>
                {footnotes.length > 0 && (
                    <ol className="mt-4 space-y-1 text-xs text-gray-500">
                        {footnotes.map((f, i) => (
                            <li key={f.key}>
                                <sup className="text-primary mr-1">{i + 1}</sup>
                                Plus the roles in the setting “{f.label}” (Admin → Settings):{' '}
                                {f.current.length ? f.current.join(', ') : 'none right now'}.
                            </li>
                        ))}
                    </ol>
                )}
            </CardContent>
        </Card>
    );
};
