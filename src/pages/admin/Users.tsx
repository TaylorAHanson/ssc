import { useEffect, useState } from 'react';
import { useUserStore } from '../../stores/userStore';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Loader2, Shield, Edit2, Trash2, Plus, Info } from 'lucide-react';
import type { RoleMapping } from '../../types';

export const Users = () => {
    const { roleMappings, isLoading, error, fetchRoleMappings, createRoleMapping, updateRoleMapping, deleteRoleMapping } = useUserStore();
    const [editingMapping, setEditingMapping] = useState<RoleMapping | null>(null);
    const [showAddModal, setShowAddModal] = useState(false);
    const [newMapping, setNewMapping] = useState({ external_role: '', internal_role: 'User' });
    const [isSaving, setIsSaving] = useState(false);

    const availableRoles = [
        { id: 'Platform Admin', name: 'Platform Admin', description: 'Full access to all system features and settings.' },
        { id: 'Governance Admin', name: 'Governance Admin', description: 'Can manage data policies, view audit logs, and oversee compliance.' },
        { id: 'Security Admin', name: 'Security Admin', description: 'Manages security settings, access controls, and security audits.' },
        { id: 'Finance Admin', name: 'Finance Admin', description: 'Can view cost data, manage budgets, and handle billing.' },
        { id: 'User', name: 'User', description: 'Standard user access to self-service features.' }
    ];

    useEffect(() => {
        fetchRoleMappings();
    }, [fetchRoleMappings]);

    const handleEditClick = (mapping: RoleMapping) => {
        setEditingMapping(mapping);
    };

    const handleDeleteClick = async (id: number) => {
        if (confirm('Are you sure you want to delete this role mapping?')) {
            await deleteRoleMapping(id);
        }
    };

    const handleSave = async () => {
        if (!editingMapping) return;
        setIsSaving(true);
        try {
            await updateRoleMapping(editingMapping.id, editingMapping.external_role, editingMapping.internal_role);
            setEditingMapping(null);
        } catch (err) {
            console.error("Failed to save role mapping", err);
        } finally {
            setIsSaving(false);
        }
    };

    const handleAddMapping = async () => {
        if (!newMapping.external_role || !newMapping.internal_role) return;
        setIsSaving(true);
        try {
            await createRoleMapping(newMapping.external_role, newMapping.internal_role);
            setShowAddModal(false);
            setNewMapping({ external_role: '', internal_role: 'User' });
        } catch (err) {
            console.error("Failed to add role mapping", err);
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading && roleMappings.length === 0) {
        return (
            <div className="flex justify-center items-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center bg-white p-6 rounded-lg border border-gray-100 shadow-sm">
                <div>
                    <h2 className="text-2xl font-bold tracking-tight text-gray-900">Role Mappings</h2>
                    <p className="text-gray-500">Map external SCIM identities (groups, roles, or users) to internal application roles.</p>
                </div>
                <Button
                    onClick={() => setShowAddModal(true)}
                    className="bg-primary text-white hover:opacity-90 transition-opacity"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Add Mapping
                </Button>
            </div>

            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
                    Error: {error}
                </div>
            )}

            <Card>
                <CardHeader>
                    <CardTitle>Active Mappings</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm text-left">
                            <thead className="text-xs text-gray-700 uppercase bg-gray-50 border-b">
                                <tr>
                                    <th className="px-6 py-3 font-medium">External Identity (SCIM)</th>
                                    <th className="px-6 py-3 font-medium">Internal Role</th>
                                    <th className="px-6 py-3 font-medium text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {roleMappings.map((mapping) => (
                                    <tr key={mapping.id} className="bg-white border-b hover:bg-gray-50 transition-colors">
                                        <td className="px-6 py-4 font-medium text-gray-900">
                                            {mapping.external_role}
                                        </td>
                                        <td className="px-6 py-4">
                                            <div className="flex items-center gap-2 group relative">
                                                <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold bg-blue-100 text-blue-800 border border-blue-200">
                                                    {mapping.internal_role}
                                                </span>
                                                <Info className="w-4 h-4 text-gray-400 cursor-help" />
                                                <div className="absolute bottom-full left-0 mb-2 w-64 p-2 bg-gray-900 text-white text-xs rounded-lg shadow-xl opacity-0 translate-y-2 invisible group-hover:opacity-100 group-hover:translate-y-0 group-hover:visible transition-all duration-200 z-50 pointer-events-none">
                                                    {availableRoles.find(r => r.id === mapping.internal_role)?.description || 'No description available'}
                                                    <div className="absolute top-full left-4 -mt-1 border-4 border-transparent border-t-gray-900" />
                                                </div>
                                            </div>
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            <Button variant="ghost" size="sm" onClick={() => handleEditClick(mapping)}>
                                                <Edit2 className="w-4 h-4 mr-1" /> Edit
                                            </Button>
                                            <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700 hover:bg-red-50" onClick={() => handleDeleteClick(mapping.id)}>
                                                <Trash2 className="w-4 h-4 mr-1" /> Delete
                                            </Button>
                                        </td>
                                    </tr>
                                ))}
                                {roleMappings.length === 0 && (
                                    <tr>
                                        <td colSpan={3} className="px-6 py-8 text-center text-gray-500">
                                            No role mappings found.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>

            {/* Edit Mapping Modal */}
            {editingMapping && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <Card className="w-full max-w-md shadow-xl animate-in fade-in zoom-in-95 duration-200">
                        <CardHeader className="border-b bg-gray-50/50">
                            <CardTitle className="flex items-center gap-2">
                                <Shield className="w-5 h-5 text-blue-600" />
                                Edit Role Mapping
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="p-6 space-y-4">
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-gray-700">External Identity (SCIM)</label>
                                <input
                                    type="text"
                                    value={editingMapping.external_role}
                                    onChange={(e) => setEditingMapping(prev => prev ? { ...prev, external_role: e.target.value } : null)}
                                    className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200"
                                />
                            </div>

                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-gray-700">Internal Role</label>
                                <select
                                    value={editingMapping.internal_role}
                                    onChange={(e) => setEditingMapping(prev => prev ? { ...prev, internal_role: e.target.value } : null)}
                                    className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200 bg-white"
                                >
                                    {availableRoles.map(role => (
                                        <option key={role.id} value={role.id} title={role.description}>
                                            {role.name}
                                        </option>
                                    ))}
                                </select>
                                <p className="text-xs text-gray-500 mt-1">
                                    {availableRoles.find(r => r.id === editingMapping.internal_role)?.description}
                                </p>
                            </div>

                            <div className="flex justify-end gap-3 pt-4 border-t mt-6">
                                <Button variant="outline" onClick={() => setEditingMapping(null)} disabled={isSaving}>
                                    Cancel
                                </Button>
                                <Button onClick={handleSave} disabled={isSaving || !editingMapping.external_role} className="bg-blue-600 hover:bg-blue-700">
                                    {isSaving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                                    Save Changes
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Add Mapping Modal */}
            {showAddModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <Card className="w-full max-w-md shadow-xl animate-in fade-in zoom-in-95 duration-200">
                        <CardHeader className="border-b bg-gray-50/50">
                            <CardTitle className="flex items-center gap-2">
                                <div className="p-1.5 bg-blue-100 rounded-md">
                                    <Plus className="w-4 h-4 text-blue-600" />
                                </div>
                                Add Role Mapping
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="p-6 space-y-4">
                            <div className="space-y-4">
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium text-gray-700">External Identity (SCIM)</label>
                                    <input
                                        type="text"
                                        value={newMapping.external_role}
                                        onChange={(e) => setNewMapping(prev => ({ ...prev, external_role: e.target.value }))}
                                        placeholder="e.g. data_engineers_group or user@company.com"
                                        className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200"
                                    />
                                    <p className="text-xs text-gray-500">The exact name of the group, role, or user email in Databricks SCIM.</p>
                                </div>
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium text-gray-700">Internal Role</label>
                                    <select
                                        value={newMapping.internal_role}
                                        onChange={(e) => setNewMapping(prev => ({ ...prev, internal_role: e.target.value }))}
                                        className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200 bg-white"
                                    >
                                        {availableRoles.map(role => (
                                            <option key={role.id} value={role.id} title={role.description}>
                                                {role.name}
                                            </option>
                                        ))}
                                    </select>
                                    <p className="text-xs text-gray-500 mt-1">
                                        {availableRoles.find(r => r.id === newMapping.internal_role)?.description}
                                    </p>
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-6 border-t mt-4">
                                <Button variant="outline" onClick={() => setShowAddModal(false)} disabled={isSaving}>
                                    Cancel
                                </Button>
                                <Button onClick={handleAddMapping} disabled={isSaving || !newMapping.external_role} className="bg-primary text-white">
                                    {isSaving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                                    Create Mapping
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
};
