import { Link } from 'react-router-dom';
import { AlertTriangle, BookOpen, Eye, KeyRound, Lock, Pencil } from 'lucide-react';
import type { RbacPlanePermission } from '../../types/rbac';
import { GUIDE_ACCESS_RULES, useGuideEnabled } from '../../lib/guide';
import { Badge, type Tone } from './ui';
import { SETTINGS_PATH } from './utils';

export function AccessRulesHeader() {
  const guideEnabled = useGuideEnabled();
  return (
    <div>
      <h2 className="flex items-center gap-2 text-xl font-bold text-gray-900">
        <KeyRound className="h-5 w-5" /> Access Rules
      </h2>
      <p className="mt-1 text-sm text-gray-500">
        Which groups hold which privileges on which securables, one row per workbook row, exported as CSV files for
        Terraform.
        {guideEnabled && (
          <>
            {' '}
            <Link to={GUIDE_ACCESS_RULES} className="inline-flex items-center gap-1 text-accent hover:underline">
              <BookOpen className="h-3.5 w-3.5" /> How Access Rules work
            </Link>
          </>
        )}
      </p>
    </div>
  );
}

const GROUP_STATUS: Record<RbacPlanePermission['group_status'], { label: string; tone: Tone; title?: string }> = {
  found: { label: 'Found in Databricks', tone: 'green' },
  account_only: {
    label: 'Found in the account',
    tone: 'green',
    title: 'An account group that isn’t assigned to this workspace.',
  },
  not_found: { label: 'Not found in Databricks', tone: 'red' },
  unverified: { label: 'Couldn’t confirm', tone: 'amber' },
  unknown: { label: 'Couldn’t check', tone: 'gray', title: 'The group lookup failed; try again later.' },
  unset: { label: 'Not set', tone: 'gray' },
};

/** One card per type: who approves its requests and edits its rows, and whether you can edit. */
export function ApproverStrip({ planes }: { planes: RbacPlanePermission[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {planes.map((p) => {
        const status = GROUP_STATUS[p.group_status] ?? GROUP_STATUS.unknown;
        return (
          <div key={p.plane} className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-gray-900">{p.label}</span>
              {p.can_edit ? (
                <Badge tone="green"><Pencil className="h-3 w-3" /> You can edit</Badge>
              ) : (
                <Badge><Eye className="h-3 w-3" /> View only</Badge>
              )}
            </div>
            <div className="mt-2 text-xs text-gray-500">Approver group</div>
            <div className="flex flex-wrap items-center gap-2">
              {p.approver_group ? (
                <code className="break-all text-sm text-gray-800">{p.approver_group}</code>
              ) : (
                <span className="inline-flex items-center gap-1 text-sm text-amber-700">
                  <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> Not set — Platform Admins approve
                </span>
              )}
              {p.approver_group && (
                <Badge tone={status.tone} title={status.title}>
                  {status.label}
                </Badge>
              )}
            </div>
            {p.group_status === 'not_found' && (
              <p className="mt-2 flex items-start gap-1.5 text-xs text-red-700">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                No Databricks group in this workspace or the account has this exact name, so approvals will only reach
                Platform Admins until it’s fixed in Admin → Settings → Access Rules.
              </p>
            )}
            {p.group_status === 'account_only' && (
              <p className="mt-2 text-xs text-gray-500">
                Not assigned to this workspace, which is fine: members are recognised from their account groups.
              </p>
            )}
            {p.group_status === 'unverified' && (
              <p className="mt-2 text-xs text-gray-500">
                This workspace has no group with this name, and the account’s groups couldn’t be checked. If it’s an
                account group, check the name matches exactly.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function NoAccessPanel() {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div className="flex items-start gap-3">
        <Lock className="mt-0.5 h-5 w-5 shrink-0 text-gray-400" />
        <div className="space-y-2 text-sm text-gray-600">
          <p className="text-base font-semibold text-gray-900">You don’t have access to Access Rules</p>
          <p>This page is open to:</p>
          <ul className="list-disc space-y-1 pl-5">
            <li>Platform Admins;</li>
            <li>the viewer roles a Platform Admin has chosen;</li>
            <li>
              members of an approver group. Each type (UC securables, UC metastore and workspace securables) has its
              own group, set in{' '}
              <Link to={SETTINGS_PATH} className="text-accent hover:underline">Admin → Settings → Access Rules</Link>.
            </li>
          </ul>
          <p>To request access, ask a Platform Admin to add you to the right approver group.</p>
        </div>
      </div>
    </div>
  );
}
