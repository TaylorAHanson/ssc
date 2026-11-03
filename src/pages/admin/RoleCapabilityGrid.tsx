import { Fragment } from 'react';
import { Check } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import type { CapabilityGrid } from '../../types';

/** Read-only "What each role can do" table, built from GET /roles/capabilities. */
export const RoleCapabilityGrid = ({ grid }: { grid: CapabilityGrid }) => {
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
