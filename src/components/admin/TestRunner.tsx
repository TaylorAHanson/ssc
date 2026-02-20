import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Play, Loader2, Terminal, Database, List, RefreshCw } from 'lucide-react';
import { api, type TestRunResponse } from '../../services/api';

export function TestRunner() {
    const [testPath, setTestPath] = useState('tests');
    const [isRunning, setIsRunning] = useState(false);
    const [output, setOutput] = useState<TestRunResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    // New state
    const [availableTests, setAvailableTests] = useState<string[]>([]);
    const [isLoadingTests, setIsLoadingTests] = useState(false);
    const [testFilter, setTestFilter] = useState('');
    const [isResetting, setIsResetting] = useState(false);
    const [isSeeding, setIsSeeding] = useState(false);
    const [dbMessage, setDbMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

    useEffect(() => {
        loadTests();
    }, []);

    const loadTests = async () => {
        setIsLoadingTests(true);
        try {
            const tests = await api.listTests();
            setAvailableTests(tests);
        } catch (err) {
            console.error('Failed to load tests:', err);
        } finally {
            setIsLoadingTests(false);
        }
    };

    const handleRunTests = async (path: string = testPath) => {
        setIsRunning(true);
        setOutput(null);
        setError(null);
        // Update input if running from list
        if (path !== testPath) setTestPath(path);

        try {
            const data = await api.runTests(path);
            setOutput(data);
        } catch (err: unknown) {
            console.error('Failed to run tests:', err);
            const errorMsg = err instanceof Error ? err.message : 'Failed to run tests';
            setError((err as any)?.response?.data?.detail || errorMsg);
        } finally {
            setIsRunning(false);
        }
    };

    const handleResetDb = async () => {
        if (!confirm('Are you sure? This will DELETE ALL DATA.')) return;
        setIsResetting(true);
        setDbMessage(null);
        try {
            await api.resetDb();
            setDbMessage({ type: 'success', text: 'Database reset successfully.' });
        } catch (err: unknown) {
            setDbMessage({ type: 'error', text: err instanceof Error ? err.message : 'Unknown error' });
        } finally {
            setIsResetting(false);
        }
    };

    const handleSeedDb = async () => {
        setIsSeeding(true);
        setDbMessage(null);
        try {
            await api.seedDb();
            setDbMessage({ type: 'success', text: 'Database seeded successfully.' });
        } catch (err: unknown) {
            setDbMessage({ type: 'error', text: err instanceof Error ? err.message : 'Unknown error' });
        } finally {
            setIsSeeding(false);
        }
    };

    const filteredTests = availableTests.filter(t => t.toLowerCase().includes(testFilter.toLowerCase()));

    return (
        <div className="space-y-6">
            {/* Database Tools */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Database className="w-5 h-5" />
                        Database Tools
                    </CardTitle>
                    <CardDescription>Manage the development database.</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center gap-4">
                        <Button
                            className="bg-red-600 hover:bg-red-700 text-white"
                            onClick={handleResetDb}
                            disabled={isResetting || isSeeding}
                        >
                            {isResetting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}
                            Reset Database
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleSeedDb}
                            disabled={isResetting || isSeeding}
                        >
                            {isSeeding ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Database className="w-4 h-4 mr-2" />}
                            Seed Data
                        </Button>
                        {dbMessage && (
                            <span className={`text-sm ${dbMessage.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                                {dbMessage.text}
                            </span>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Test Runner */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Terminal className="w-5 h-5" />
                        Backend Test Runner
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex gap-4">
                        <div className="flex-1">
                            <Input
                                value={testPath}
                                onChange={(e) => setTestPath(e.target.value)}
                                placeholder="tests/unit/..."
                                className="font-mono"
                            />
                            <p className="text-xs text-gray-500 mt-1">
                                Path relative to backend root. Default: "tests" (runs all).
                            </p>
                        </div>
                        <Button
                            onClick={() => handleRunTests(testPath)}
                            disabled={isRunning || !testPath}
                            className="min-w-[120px]"
                        >
                            {isRunning ? (
                                <>
                                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                    Running...
                                </>
                            ) : (
                                <>
                                    <Play className="w-4 h-4 mr-2" />
                                    Run Tests
                                </>
                            )}
                        </Button>
                    </div>

                    {error && (
                        <div className="p-4 bg-red-50 text-red-700 rounded-md border border-red-200">
                            <p className="font-semibold">Error:</p>
                            <p>{error}</p>
                        </div>
                    )}

                    {output && (
                        <div className="space-y-2">
                            <div className={`p-4 rounded-md border text-sm font-mono overflow-auto max-h-[500px] whitespace-pre-wrap ${output.exit_code === 0
                                ? 'bg-gray-900 text-green-400 border-gray-700'
                                : 'bg-gray-900 text-red-400 border-red-900'
                                }`}>
                                <div className="mb-2 text-gray-500 border-b border-gray-700 pb-2">
                                    $ {output.command.join(' ')}
                                </div>
                                {output.stdout}
                                {output.stderr}
                            </div>
                            <div className="text-sm text-gray-500 text-right">
                                Exit Code: {output.exit_code}
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Available Tests */}
            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <CardTitle className="flex items-center gap-2">
                            <List className="w-5 h-5" />
                            Available Tests
                        </CardTitle>
                        <Button variant="ghost" size="sm" onClick={loadTests} disabled={isLoadingTests}>
                            <RefreshCw className={`w-4 h-4 ${isLoadingTests ? 'animate-spin' : ''}`} />
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="mb-4">
                        <Input
                            placeholder="Filter tests..."
                            value={testFilter}
                            onChange={(e) => setTestFilter(e.target.value)}
                        />
                    </div>
                    {isLoadingTests ? (
                        <div className="flex justify-center py-8">
                            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                        </div>
                    ) : (
                        <div className="border rounded-md divide-y max-h-[400px] overflow-y-auto">
                            {filteredTests.length === 0 ? (
                                <div className="p-4 text-center text-gray-500">No tests found</div>
                            ) : (
                                filteredTests.map((test) => (
                                    <div key={test} className="flex items-center justify-between p-3 hover:bg-gray-50">
                                        <span className="font-mono text-sm truncate mr-4" title={test}>
                                            {test}
                                        </span>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => handleRunTests(test)}
                                            disabled={isRunning}
                                        >
                                            <Play className="w-3 h-3 text-green-600" />
                                        </Button>
                                    </div>
                                ))
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
