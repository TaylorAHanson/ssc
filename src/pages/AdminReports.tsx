import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import { Switch } from '../components/ui/switch';
import { Loader2, Plus, Edit2, Trash2, AlertCircle, Search } from 'lucide-react';
import {
    listSubscriptions, createSubscription, updateSubscription, deleteSubscription,
    listExecutions
} from '../services/api';
import type { ReportSubscription, ReportSubscriptionCreate, ExecutionSummary, PromptDef } from '../types';
import { format } from 'date-fns';
import cronstrue from 'cronstrue';

export function AdminReports() {
    const [subscriptions, setSubscriptions] = useState<ReportSubscription[]>([]);
    const [executions, setExecutions] = useState<ExecutionSummary[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [showForm, setShowForm] = useState(false);
    const [editingSub, setEditingSub] = useState<ReportSubscription | null>(null);
    const [searchQuery, setSearchQuery] = useState('');

    // Form State
    const [formData, setFormData] = useState<ReportSubscriptionCreate>({
        name: '',
        subscribers: '',
        schedule_cron: '0 9 * * *',
        prompts: [{ label: 'Section 1', prompt: '' }],
        is_active: true
    });

    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        setIsLoading(true);
        try {
            const [subs, execs] = await Promise.all([
                listSubscriptions(),
                listExecutions()
            ]);
            setSubscriptions(subs);
            setExecutions(execs);
            setError(null);
        } catch (err) {
            console.error(err);
            setError('Failed to load reports data');
        } finally {
            setIsLoading(false);
        }
    };

    const handleCreateNew = () => {
        setEditingSub(null);
        setFormData({
            name: '',
            subscribers: '',
            schedule_cron: '0 9 * * *',
            prompts: [{ label: 'Section 1', prompt: '' }],
            is_active: true
        });
        setShowForm(true);
    };

    const handleEdit = (sub: ReportSubscription) => {
        setEditingSub(sub);
        setFormData({
            name: sub.name,
            subscribers: sub.subscribers,
            schedule_cron: sub.schedule_cron,
            prompts: sub.prompts.length ? sub.prompts : [{ label: 'Section 1', prompt: '' }],
            is_active: sub.is_active
        });
        setShowForm(true);
    };

    const handleDelete = async (id: string) => {
        if (!confirm('Are you sure you want to delete this subscription?')) return;
        try {
            await deleteSubscription(id);
            await loadData();
        } catch (err) {
            setError('Failed to delete subscription');
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsSaving(true);
        setError(null);
        try {
            if (editingSub) {
                await updateSubscription(editingSub.id, formData);
            } else {
                await createSubscription(formData);
            }
            setShowForm(false);
            await loadData();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to save subscription');
        } finally {
            setIsSaving(false);
        }
    };

    const addPrompt = () => {
        setFormData({ ...formData, prompts: [...formData.prompts, { label: '', prompt: '' }] });
    };

    const removePrompt = (index: number) => {
        const newPrompts = [...formData.prompts];
        newPrompts.splice(index, 1);
        setFormData({ ...formData, prompts: newPrompts });
    };

    const updatePrompt = (index: number, field: keyof PromptDef, value: string) => {
        const newPrompts = [...formData.prompts];
        newPrompts[index] = { ...newPrompts[index], [field]: value };
        setFormData({ ...formData, prompts: newPrompts });
    };

    const filteredSubscriptions = subscriptions.filter(sub =>
        sub.name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    if (isLoading && !subscriptions.length) {
        return (
            <div className="flex items-center justify-center p-8">
                <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
        );
    }

    const getCronDescription = (cron: string) => {
        try {
            return cronstrue.toString(cron);
        } catch (e) {
            return 'Invalid cron expression';
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900 mb-2">Reports</h1>
                    <p className="text-gray-600">Manage automated agent-generated reports</p>
                </div>
                <div className="flex items-center gap-3">
                    {!showForm && (
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                            <Input
                                placeholder="Search reports..."
                                className="pl-9 w-64"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>
                    )}
                    <Button onClick={handleCreateNew} disabled={showForm}>
                        <Plus className="w-4 h-4 mr-2" />
                        New Subscription
                    </Button>
                </div>
            </div>

            {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-md flex items-center gap-2 text-red-800">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {showForm ? (
                <Card>
                    <CardHeader>
                        <CardTitle>{editingSub ? 'Edit Subscription' : 'New Subscription'}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <form onSubmit={handleSubmit} className="space-y-6">
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="name">Report Name</Label>
                                    <Input
                                        id="name"
                                        value={formData.name}
                                        onChange={e => setFormData({ ...formData, name: e.target.value })}
                                        required
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="cron">Schedule (Cron)</Label>
                                    <Input
                                        id="cron"
                                        value={formData.schedule_cron}
                                        onChange={e => setFormData({ ...formData, schedule_cron: e.target.value })}
                                        placeholder="0 9 * * *"
                                        required
                                    />
                                    <p className="text-xs text-blue-600 mt-1">
                                        {getCronDescription(formData.schedule_cron)}
                                    </p>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="subscribers">Subscribers (comma separated emails)</Label>
                                <Input
                                    id="subscribers"
                                    value={formData.subscribers}
                                    onChange={e => setFormData({ ...formData, subscribers: e.target.value })}
                                    required
                                />
                            </div>

                            <div className="space-y-4">
                                <div className="flex items-center justify-between">
                                    <Label>Prompts</Label>
                                    <Button type="button" variant="outline" size="sm" onClick={addPrompt}>Add Section</Button>
                                </div>
                                {formData.prompts.map((p, i) => (
                                    <div key={i} className="p-4 border border-gray-200 rounded-md space-y-3 relative">
                                        <div className="absolute right-2 top-2">
                                            <Button variant="ghost" size="sm" onClick={() => removePrompt(i)} className="text-red-500 hover:text-red-700">
                                                <Trash2 className="w-4 h-4" />
                                            </Button>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Section Label</Label>
                                            <Input
                                                value={p.label}
                                                onChange={e => updatePrompt(i, 'label', e.target.value)}
                                                placeholder="e.g. Daily Summary"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Agent Prompt</Label>
                                            <Textarea
                                                value={p.prompt}
                                                onChange={e => updatePrompt(i, 'prompt', e.target.value)}
                                                placeholder="Instructions for the agent..."
                                                rows={4}
                                            />
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div className="flex items-center space-x-2">
                                <Switch
                                    id="active"
                                    checked={formData.is_active}
                                    onCheckedChange={c => setFormData({ ...formData, is_active: c })}
                                />
                                <Label htmlFor="active">Active</Label>
                            </div>

                            <div className="flex justify-end gap-2">
                                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
                                <Button type="submit" disabled={isSaving}>
                                    {isSaving && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
                                    Save Subscription
                                </Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid grid-cols-1 gap-6">
                    {filteredSubscriptions.map(sub => (
                        <Card key={sub.id} className={!sub.is_active ? 'opacity-70 bg-gray-50' : ''}>
                            <CardContent className="p-6">
                                <div className="flex items-start justify-between">
                                    <div>
                                        <h3 className="text-lg font-bold flex items-center gap-2">
                                            {sub.name}
                                            {!sub.is_active && <span className="text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded">Inactive</span>}
                                        </h3>
                                        <p className="text-sm text-gray-500 mt-1">
                                            Schedule: <code className="bg-gray-100 px-1 rounded">{sub.schedule_cron}</code>
                                            <span className="text-xs text-blue-600 ml-2 italic">({getCronDescription(sub.schedule_cron)})</span> •
                                            Next Run: {sub.next_run_at ? format(new Date(sub.next_run_at), 'MMM d, HH:mm') : 'N/A'}
                                        </p>
                                        <p className="text-sm text-gray-500 mt-1">
                                            Subscribers: {sub.subscribers}
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Button variant="outline" size="sm" onClick={() => handleEdit(sub)}>
                                            <Edit2 className="w-4 h-4 mr-2" /> Edit
                                        </Button>
                                        <Button variant="ghost" size="sm" onClick={() => handleDelete(sub.id)} className="text-red-500">
                                            <Trash2 className="w-4 h-4" />
                                        </Button>
                                    </div>
                                </div>

                                <div className="mt-4 pt-4 border-t border-gray-100">
                                    <h4 className="text-sm font-semibold mb-2">Recent Executions</h4>
                                    <div className="space-y-1">
                                        {executions.filter(e => e.status !== 'pending' && e.title.includes(sub.name)).slice(0, 3).map(e => (
                                            <div key={e.id} className="flex items-center text-sm gap-2">
                                                <span className={`w-2 h-2 rounded-full ${e.status === 'completed' ? 'bg-green-500' :
                                                    e.status === 'failed' ? 'bg-red-500' : 'bg-gray-400'
                                                    }`} />
                                                <span className="text-gray-600">{format(new Date(e.created_at), 'MMM d, HH:mm')} - {e.status}</span>
                                            </div>
                                        ))}
                                        {!executions.some(e => e.title.includes(sub.name)) && (
                                            <span className="text-xs text-gray-400">No executions found</span>
                                        )}
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                    {filteredSubscriptions.length === 0 && (
                        <p className="text-center text-gray-500 py-8">
                            {searchQuery ? `No reports matching "${searchQuery}"` : 'No subscriptions configured.'}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
