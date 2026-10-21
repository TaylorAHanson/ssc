import { useState } from 'react';
import { format } from 'date-fns';
import { ArrowRight, Calendar, Trash2 } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { useRequestStore } from '../../stores/requestStore';
import type { Delegation } from '../../types';

/** One line per delegation, in either direction. */
export function DelegationStrips({ incoming }: { incoming: Delegation[] }) {
  const delegations = useRequestStore((state) => state.delegations);
  const removeDelegation = useRequestStore((state) => state.removeDelegation);
  if (delegations.length === 0 && incoming.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {delegations.map((del) => (
        <div key={del.id} className="flex flex-wrap items-center gap-2 rounded-md border border-blue-200 bg-blue-50/60 px-3 py-1.5 text-sm text-blue-900">
          <Calendar className="w-4 h-4 shrink-0" />
          <span>
            Your approvals go to <strong>{del.delegatee_email}</strong> from{' '}
            {format(new Date(del.start_date), 'MMM d, yyyy')} to {format(new Date(del.end_date), 'MMM d, yyyy')}.
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-7 px-2 text-red-600 hover:text-red-700 hover:bg-red-50"
            onClick={() => removeDelegation(del.id)}
          >
            <Trash2 className="w-3.5 h-3.5 mr-1" />
            Revoke
          </Button>
        </div>
      ))}
      {incoming.map((del) => (
        <div key={del.id} className="flex items-center gap-2 rounded-md border border-green-200 bg-green-50/60 px-3 py-1.5 text-sm text-green-900">
          <ArrowRight className="w-4 h-4 shrink-0" />
          <span>
            You're covering approvals for <strong>{del.delegator_email}</strong> until{' '}
            {format(new Date(del.end_date), 'MMM d, yyyy')}.
          </span>
        </div>
      ))}
    </div>
  );
}

/** Set up "delegate all my approvals" for a date range. */
export function DelegationForm({ onClose }: { onClose: () => void }) {
  const addDelegation = useRequestStore((state) => state.addDelegation);
  const [email, setEmail] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!email.trim() || !startDate || !endDate) {
      setError('Fill in who covers for you and both dates.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await addDelegation({
        delegatee_email: email.trim(),
        start_date: new Date(startDate).toISOString(),
        end_date: new Date(endDate).toISOString(),
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add delegation');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 animate-in fade-in slide-in-from-top-2 duration-200">
      <p className="text-sm font-semibold text-gray-900 mb-2">Out of office: delegate all your approvals</p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_auto] md:items-end">
        <div className="space-y-1">
          <label htmlFor="ooo-delegate" className="text-xs font-medium text-gray-700">Delegate to</label>
          <Input
            id="ooo-delegate"
            className="h-9"
            placeholder="delegate@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="ooo-start" className="text-xs font-medium text-gray-700">Start date</label>
          <Input id="ooo-start" className="h-9" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label htmlFor="ooo-end" className="text-xs font-medium text-gray-700">End date</label>
          <Input id="ooo-end" className="h-9" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={submit} disabled={saving}>
            {saving ? 'Saving...' : 'Enable delegation'}
          </Button>
          <Button size="sm" variant="outline" onClick={onClose}>Cancel</Button>
        </div>
      </div>
      <p className="mt-2 text-xs text-gray-500">This person receives all your approval tasks during the period.</p>
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    </div>
  );
}
