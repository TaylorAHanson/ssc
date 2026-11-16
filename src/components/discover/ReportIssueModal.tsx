import { useEffect, useState } from 'react';
import { CheckCircle2, Flag, Loader2, X } from 'lucide-react';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Textarea } from '../ui/textarea';
import { Label } from '../ui/label';
import { reportDataIssue } from '../../services/api';
import { errorMessage } from '../../lib/errors';

interface ReportIssueModalProps {
  /** The asset the report is about: its id, and the name to show for it. */
  asset: { id: string; name: string };
  onClose: () => void;
}

/** Report a problem with a data asset. Starts the data issue workflow: an email, then a task for the team. */
export function ReportIssueModal({ asset, onClose }: ReportIssueModalProps) {
  const [summary, setSummary] = useState('');
  const [details, setDetails] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const submit = async () => {
    if (summary.trim().length < 3) {
      setError('Please say what is wrong in a few words.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await reportDataIssue(asset.id, summary.trim(), details.trim() || undefined);
      setSent(true);
    } catch (e) {
      setError(errorMessage(e, 'Could not send the report. Please try again.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4 animate-in fade-in duration-200"
      role="dialog"
      aria-modal="true"
      aria-label="Report a data issue"
    >
      <Card className="w-full max-w-lg shadow-xl animate-in zoom-in-95 duration-200 max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <Flag className="w-4 h-4 text-amber-600" /> Report a data issue
          </h2>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <CardContent className="p-6 space-y-4 overflow-y-auto">
          {sent ? (
            <div className="py-6 text-center">
              <CheckCircle2 className="w-12 h-12 text-green-500 mx-auto mb-3" />
              <p className="text-base font-medium text-gray-900">Thanks, your report is on its way</p>
              <p className="text-sm text-gray-500 mt-1">
                The team has been emailed and will look into <span className="font-mono">{asset.name}</span>.
                You can follow it under Requests.
              </p>
            </div>
          ) : (
            <>
              <p className="text-sm text-gray-600">
                Something wrong with <span className="font-mono text-gray-900 break-all">{asset.name}</span>? Tell us what you
                saw and the team will take it from here.
              </p>
              <div className="space-y-1.5">
                <Label htmlFor="issue-summary">What's wrong?</Label>
                <Input
                  id="issue-summary"
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                  placeholder="e.g. Nothing new has landed since last Friday"
                  maxLength={300}
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="issue-details">More detail (optional)</Label>
                <Textarea
                  id="issue-details"
                  value={details}
                  onChange={(e) => setDetails(e.target.value)}
                  rows={4}
                  maxLength={4000}
                  placeholder="Which columns or dates, what you expected to see, and how you noticed."
                />
              </div>
              {error && (
                <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">{error}</div>
              )}
            </>
          )}
        </CardContent>

        <div className="flex justify-end gap-3 px-6 py-4 border-t">
          {sent ? (
            <Button onClick={onClose}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={onClose} disabled={submitting}>
                Cancel
              </Button>
              <Button onClick={submit} disabled={submitting}>
                {submitting ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" /> Sending...
                  </span>
                ) : (
                  'Send report'
                )}
              </Button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
