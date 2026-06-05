import { useEffect, useMemo, useState } from 'react';
import { Bug, Lightbulb, MessageSquare, X, Loader2, CheckCircle2 } from 'lucide-react';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Textarea } from '../ui/textarea';
import { Label } from '../ui/label';
import { cn } from '../../lib/utils';
import { submitFeedback, type FeedbackType } from '../../services/api';
import { getDiagnostics } from '../../lib/diagnostics';

interface FeedbackModalProps {
  open: boolean;
  onClose: () => void;
  initialType?: FeedbackType;
}

const TYPE_OPTIONS: { value: FeedbackType; label: string; icon: React.ReactNode; hint: string }[] = [
  { value: 'feedback', label: 'Feedback', icon: <MessageSquare className="w-4 h-4" />, hint: 'Share a thought or comment' },
  { value: 'feature', label: 'Feature request', icon: <Lightbulb className="w-4 h-4" />, hint: 'Suggest an idea or improvement' },
  { value: 'bug', label: 'Report a bug', icon: <Bug className="w-4 h-4" />, hint: 'Something is broken or not working' },
];

const SEVERITIES = ['low', 'medium', 'high', 'critical'];

export function FeedbackModal({ open, onClose, initialType = 'feedback' }: FeedbackModalProps) {
  const [type, setType] = useState<FeedbackType>(initialType);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [severity, setSeverity] = useState('medium');
  const [includeDiagnostics, setIncludeDiagnostics] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Snapshot diagnostics counts when the modal opens (for the bug-report hint).
  const diagSnapshot = useMemo(() => (open ? getDiagnostics() : null), [open]);

  useEffect(() => {
    if (open) {
      setType(initialType);
      setTitle('');
      setDescription('');
      setSeverity('medium');
      setIncludeDiagnostics(true);
      setError(null);
      setSuccess(false);
      setSubmitting(false);
    }
  }, [open, initialType]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleSubmit = async () => {
    if (!title.trim()) {
      setError('Please enter a short title.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const diag = type === 'bug' && includeDiagnostics ? getDiagnostics() : null;
      await submitFeedback({
        type,
        title: title.trim(),
        description: description.trim() || undefined,
        severity: type === 'bug' ? severity : undefined,
        source: 'web',
        page_url: diag?.page_url,
        user_agent: diag?.user_agent,
        app_version: diag?.app_version,
        console_logs: diag?.console_logs,
        network_errors: diag?.network_errors,
      });
      setSuccess(true);
      setTimeout(() => onClose(), 1400);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const activeType = TYPE_OPTIONS.find((t) => t.value === type)!;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4 animate-in fade-in duration-200">
      <Card className="w-full max-w-lg shadow-xl animate-in zoom-in-95 duration-200 max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-900">Send feedback</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <CardContent className="p-6 space-y-4 overflow-y-auto">
          {success ? (
            <div className="py-8 text-center">
              <CheckCircle2 className="w-12 h-12 text-green-500 mx-auto mb-3" />
              <p className="text-base font-medium text-gray-900">Thanks for your feedback!</p>
              <p className="text-sm text-gray-500 mt-1">
                It's been sent to the admins for review.
              </p>
            </div>
          ) : (
            <>
              {/* Type selector */}
              <div className="space-y-2">
                <Label>What would you like to share?</Label>
                <div className="grid grid-cols-3 gap-2">
                  {TYPE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setType(opt.value)}
                      className={cn(
                        'flex flex-col items-center gap-1.5 rounded-lg border px-2 py-3 text-xs font-medium transition-colors',
                        type === opt.value
                          ? 'border-primary bg-primary/5 text-primary'
                          : 'border-gray-200 text-gray-600 hover:bg-gray-50',
                      )}
                    >
                      {opt.icon}
                      {opt.label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-400">{activeType.hint}</p>
              </div>

              {/* Title */}
              <div className="space-y-1.5">
                <Label htmlFor="feedback-title">Title</Label>
                <Input
                  id="feedback-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={
                    type === 'bug'
                      ? 'e.g. Approvals page fails to load'
                      : type === 'feature'
                        ? 'e.g. Export requests to CSV'
                        : 'e.g. The new catalog view is great'
                  }
                  maxLength={300}
                />
              </div>

              {/* Description */}
              <div className="space-y-1.5">
                <Label htmlFor="feedback-description">
                  {type === 'bug' ? 'What happened? (steps to reproduce)' : 'Details'}
                </Label>
                <Textarea
                  id="feedback-description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  placeholder={
                    type === 'bug'
                      ? 'Describe what you did, what you expected, and what actually happened.'
                      : 'Add any details that would help us understand.'
                  }
                />
              </div>

              {/* Bug-only: severity + diagnostics */}
              {type === 'bug' && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="feedback-severity">Severity</Label>
                    <select
                      id="feedback-severity"
                      value={severity}
                      onChange={(e) => setSeverity(e.target.value)}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 capitalize"
                    >
                      {SEVERITIES.map((s) => (
                        <option key={s} value={s} className="capitalize">
                          {s}
                        </option>
                      ))}
                    </select>
                  </div>

                  <label className="flex items-start gap-2 rounded-md bg-gray-50 border border-gray-200 p-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={includeDiagnostics}
                      onChange={(e) => setIncludeDiagnostics(e.target.checked)}
                      className="mt-0.5"
                    />
                    <span className="text-xs text-gray-600">
                      <span className="font-medium text-gray-800">Attach diagnostics</span> to help us
                      debug. Includes your browser info plus a snapshot of{' '}
                      {diagSnapshot?.console_logs.length ?? 0} recent console message(s) and{' '}
                      {diagSnapshot?.network_errors.length ?? 0} failed network request(s). No page
                      content or form data is captured.
                    </span>
                  </label>
                </>
              )}

              {error && (
                <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
                  {error}
                </div>
              )}
            </>
          )}
        </CardContent>

        {!success && (
          <div className="flex justify-end gap-3 px-6 py-4 border-t">
            <Button variant="outline" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={submitting}>
              {submitting ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Submitting...
                </span>
              ) : (
                'Submit'
              )}
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
