import { useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Upload, Loader2, CheckCircle2, FileSpreadsheet } from 'lucide-react';
import { uploadTrainingData } from '../../services/api';

type UploadStats = {
  processed?: number;
  added?: number;
  updated?: number;
  skipped?: number;
  errors?: number;
};

/**
 * Admin tool for bulk-loading training completion records from an LMS export.
 *
 * Wraps POST /training/upload (CSV ingest). The resulting completions feed both
 * the Training page progress view and workflow training gates that bind to a
 * specific course code.
 */
export function TrainingUpload() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [stats, setStats] = useState<UploadStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleUpload = async () => {
    if (!file) return;
    setIsUploading(true);
    setError(null);
    setStats(null);
    try {
      const result = await uploadTrainingData(file);
      setStats(result.stats || {});
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  const reset = () => {
    setFile(null);
    setStats(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <div className="max-w-2xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Upload className="w-5 h-5" />
            Training Completion Upload
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-gray-600">
            Upload a CSV export of training completions from your LMS. Existing
            records for the same learner and course are updated; new ones are
            added. These completions power user training progress and any
            workflow gate that requires a specific course.
          </p>

          <div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
            <p className="font-medium text-gray-700 mb-1">Expected columns</p>
            <code className="block">
              updated_learner_email, Course_Name, Course_Code, completed_timestamp, Status
            </code>
          </div>

          <div className="flex items-center gap-3">
            <input
              ref={inputRef}
              type="file"
              accept=".csv"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setStats(null);
                setError(null);
              }}
              className="block w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-primary/90"
            />
          </div>

          {file && (
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <FileSpreadsheet className="w-4 h-4 text-gray-400" />
              <span className="truncate">{file.name}</span>
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button
              onClick={handleUpload}
              disabled={!file || isUploading}
              className="flex items-center gap-2"
            >
              {isUploading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4" />
                  Upload CSV
                </>
              )}
            </Button>
            {(file || stats || error) && (
              <Button variant="outline" onClick={reset} disabled={isUploading}>
                Reset
              </Button>
            )}
          </div>

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
              {error}
            </div>
          )}

          {stats && (
            <div className="rounded-md border border-green-200 bg-green-50 p-4">
              <div className="mb-2 flex items-center gap-2 text-sm font-medium text-green-800">
                <CheckCircle2 className="w-4 h-4" />
                Upload complete
              </div>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm text-gray-700 sm:grid-cols-5">
                <Stat label="Processed" value={stats.processed} />
                <Stat label="Added" value={stats.added} />
                <Stat label="Updated" value={stats.updated} />
                <Stat label="Skipped" value={stats.skipped} />
                <Stat label="Errors" value={stats.errors} />
              </dl>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="text-lg font-semibold text-gray-900">{value ?? 0}</dd>
    </div>
  );
}
