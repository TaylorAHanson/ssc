import { useState, useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, FileText, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { uploadTrainingData } from '../../services/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';

export function TrainingUpload() {
    const [uploading, setUploading] = useState(false);
    const [success, setSuccess] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [stats, setStats] = useState<any>(null);

    const onDrop = useCallback(async (acceptedFiles: File[]) => {
        const file = acceptedFiles[0];
        if (!file) return;

        setUploading(true);
        setSuccess(null);
        setError(null);
        setStats(null);

        try {
            const response = await uploadTrainingData(file);
            setSuccess('Training data synced successfully');
            setStats(response.stats);
        } catch (err: any) {
            setError(err.message || 'Failed to upload file');
        } finally {
            setUploading(false);
        }
    }, []);

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        accept: {
            'text/csv': ['.csv'],
        },
        maxFiles: 1,
        multiple: false
    });

    return (
        <Card className="max-w-2xl mx-auto shadow-md">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Upload className="w-5 h-5" />
                    Import Training Data
                </CardTitle>
                <CardDescription>
                    Upload a CSV file containing training completion records.
                    This will update the training status for all users found in the file.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">

                {/* Dropzone */}
                <div
                    {...getRootProps()}
                    className={`
            border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all
            flex flex-col items-center justify-center gap-4
            ${isDragActive ? 'border-primary bg-primary/5' : 'border-gray-200 hover:bg-gray-50 hover:border-gray-300'}
            ${uploading ? 'opacity-50 pointer-events-none' : ''}
          `}
                >
                    <input {...getInputProps()} />
                    <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center text-gray-500">
                        {uploading ? (
                            <RefreshCw className="w-8 h-8 animate-spin text-primary" />
                        ) : (
                            <FileText className="w-8 h-8" />
                        )}
                    </div>
                    <div>
                        <p className="font-medium text-gray-900">
                            {isDragActive ? 'Drop the CSV file here' : 'Drag & drop training CSV here'}
                        </p>
                        <p className="text-sm text-gray-500 mt-1">or click to select file</p>
                    </div>
                    <p className="text-xs text-gray-400">Accepted format: .csv</p>
                </div>

                {/* Status Messages */}
                {error && (
                    <div className="p-4 rounded-lg bg-red-50 text-red-700 flex items-start gap-3 text-sm">
                        <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="font-bold">Upload Failed</p>
                            <p>{error}</p>
                        </div>
                    </div>
                )}

                {success && (
                    <div className="space-y-4 animate-in fade-in slide-in-from-top-2">
                        <div className="p-4 rounded-lg bg-green-50 text-green-700 flex items-center gap-3 text-sm">
                            <CheckCircle className="w-5 h-5 flex-shrink-0" />
                            <p className="font-bold">{success}</p>
                        </div>

                        {stats && (
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div className="bg-gray-50 p-3 rounded-lg text-center">
                                    <p className="text-xs text-gray-500 uppercase tracking-wider">Processed</p>
                                    <p className="text-xl font-bold text-gray-900">{stats.processed}</p>
                                </div>
                                <div className="bg-green-50 p-3 rounded-lg text-center">
                                    <p className="text-xs text-green-600 uppercase tracking-wider">Added</p>
                                    <p className="text-xl font-bold text-green-700">{stats.added}</p>
                                </div>
                                <div className="bg-blue-50 p-3 rounded-lg text-center">
                                    <p className="text-xs text-blue-600 uppercase tracking-wider">Updated</p>
                                    <p className="text-xl font-bold text-blue-700">{stats.updated}</p>
                                </div>
                                <div className="bg-yellow-50 p-3 rounded-lg text-center">
                                    <p className="text-xs text-yellow-600 uppercase tracking-wider">Skipped</p>
                                    <p className="text-xl font-bold text-yellow-700">{stats.skipped}</p>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
