import { Link } from 'react-router-dom';
import { AlertTriangle, BookOpen, Eye, KeyRound, Lock, Pencil } from 'lucide-react';
import type { RbacPlanePermission } from '../../types/rbac';
import { GUIDE_ACCESS_RULES, useGuideEnabled } from '../../lib/guide';
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

const GROUP_STATUS: Record<RbacPlanePermission['group_status'], { label: string; dot: string; detail?: string }> = {
  found: { label: 'Found in Databricks', dot: 'bg-green-500' },
  account_only: {
    label: 'Found in the account',
    dot: 'bg-green-500',
    detail: 'Not assigned to this workspace, which is fine: members are recognised from their account groups.',
  },
  not_found: {
    label: 'Not found in Databricks',
    dot: 'bg-red-500',
    detail: 'No group in this workspace or the account has this exact name, so only Platform Admins get these approvals.',
  },
  unverified: {
    label: 'Couldn’t confirm',
    dot: 'bg-amber-500',
    detail: 'This workspace has no group with this name, and the account’s groups couldn’t be checked. If it’s an account group, check the name matches exactly.',
  },
  unknown: { label: 'Couldn’t check', dot: 'bg-gray-400', detail: 'The group lookup failed; try again later.' },
  unset: { label: 'Not set', dot: 'bg-gray-400' },
};

/** One line: each type's approver group, whether Databricks knows it, and whether you can edit that type. */
export function ApproverStrip({ planes }: { planes: RbacPlanePermission[] }) {
  const missing = planes.filter((p) => p.group_status === 'not_found');
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs shadow-sm">
        <span className="font-medium text-gray-500">Approvers</span>
        {planes.map((p) => (
          <ApproverItem key={p.plane} plane={p} />
        ))}
      </div>
      {missing.length > 0 && (
        <p className="flex items-start gap-1.5 text-xs text-red-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            {missing.map((p) => p.label).join(', ')}: the approver group isn’t in Databricks, so only Platform Admins
            get these approvals until it’s fixed in{' '}
            <Link to={SETTINGS_PATH} className="underline">Admin → Settings → Access Rules</Link>.
          </span>
        </p>
      )}
    </div>
  );
}

function ApproverItem({ plane: p }: { plane: RbacPlanePermission }) {
  const status = GROUP_STATUS[p.group_status] ?? GROUP_STATUS.unknown;
  const access = p.can_edit ? 'You can edit' : 'View only';
  const AccessIcon = p.can_edit ? Pencil : Eye;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span title={access} className="inline-flex shrink-0">
        <AccessIcon className={`h-3.5 w-3.5 ${p.can_edit ? 'text-green-600' : 'text-gray-400'}`} aria-hidden="true" />
        <span className="sr-only">{access}:</span>
      </span>
      <span className="text-gray-700">{p.label}</span>
      {p.approver_group ? (
        <>
          <code className="break-all text-gray-900">{p.approver_group}</code>
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${status.dot}`}
            title={status.detail ? `${status.label}. ${status.detail}` : status.label}
            role="img"
            aria-label={status.label}
          />
        </>
      ) : (
        <span className="inline-flex items-center gap-1 text-amber-700" title="No approver group is set, so Platform Admins approve.">
          <AlertTriangle className="h-3 w-3" aria-hidden="true" /> Not set
        </span>
      )}
    </span>
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
