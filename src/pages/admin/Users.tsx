
import { useEffect, useState } from 'react';
import { useUserStore } from '../../stores/userStore';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Loader2, Shield, Edit2, Check } from 'lucide-react';
import type { User } from '../../types';

export const Users = () => {
    const { users, roles, isLoading, error, fetchUsers, fetchRoles, updateUserRoles, createUser } = useUserStore();
    const [editingUser, setEditingUser] = useState<User | null>(null);
    const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
    const [showAddModal, setShowAddModal] = useState(false);
    const [newUser, setNewUser] = useState({ email: '', fullName: '' });
    const [selectedAddRoleIds, setSelectedAddRoleIds] = useState<string[]>([]);
    const [isSaving, setIsSaving] = useState(false);

    useEffect(() => {
        fetchUsers();
        fetchRoles();
    }, [fetchUsers, fetchRoles]);

    const handleEditClick = (user: User) => {
        setEditingUser(user);
        setSelectedRoleIds(user.roles.map(r => r.id));
    };

    const handleRoleToggle = (roleId: string) => {
        setSelectedRoleIds(prev =>
            prev.includes(roleId)
                ? prev.filter(id => id !== roleId)
                : [...prev, roleId]
        );
    };

    const handleAddRoleToggle = (roleId: string) => {
        setSelectedAddRoleIds(prev =>
            prev.includes(roleId)
                ? prev.filter(id => id !== roleId)
                : [...prev, roleId]
        );
    };

    const handleSave = async () => {
        if (!editingUser) return;
        setIsSaving(true);
        try {
            await updateUserRoles(editingUser.id, selectedRoleIds);
            setEditingUser(null);
        } catch (err) {
            console.error("Failed to save roles", err);
        } finally {
            setIsSaving(false);
        }
    };

    const handleAddUser = async () => {
        if (!newUser.email || !newUser.fullName) return;
        setIsSaving(true);
        try {
            await createUser(newUser.email, newUser.fullName, selectedAddRoleIds);
            setShowAddModal(false);
            setNewUser({ email: '', fullName: '' });
            setSelectedAddRoleIds([]);
        } catch (err) {
            console.error("Failed to add user", err);
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading && users.length === 0) {
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
                    <h2 className="text-2xl font-bold tracking-tight text-gray-900">User Management</h2>
                    <p className="text-gray-500">Manage user access and roles.</p>
                </div>
                <Button
                    onClick={() => setShowAddModal(true)}
                    className="bg-primary text-white hover:opacity-90 transition-opacity"
                >
                    Add User
                </Button>
            </div>

            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
                    Error: {error}
                </div>
            )}

            <Card>
                <CardHeader>
                    <CardTitle>Users</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm text-left">
                            <thead className="text-xs text-gray-700 uppercase bg-gray-50 border-b">
                                <tr>
                                    <th className="px-6 py-3 font-medium">User</th>
                                    <th className="px-6 py-3 font-medium">Email</th>
                                    <th className="px-6 py-3 font-medium">Roles</th>
                                    <th className="px-6 py-3 font-medium">Status</th>
                                    <th className="px-6 py-3 font-medium text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {users.map((user) => (
                                    <tr key={user.id} className="bg-white border-b hover:bg-gray-50 transition-colors">
                                        <td className="px-6 py-4 font-medium text-gray-900">
                                            <div className="flex items-center gap-2">
                                                <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold">
                                                    {(user.full_name || user.email)[0].toUpperCase()}
                                                </div>
                                                {user.full_name || 'N/A'}
                                            </div>
                                        </td>
                                        <td className="px-6 py-4 text-gray-600">{user.email}</td>
                                        <td className="px-6 py-4">
                                            <div className="flex flex-wrap gap-1">
                                                {user.roles.map(role => (
                                                    <span key={role.id} className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold bg-blue-100 text-blue-800 border border-blue-200">
                                                        {role.name}
                                                    </span>
                                                ))}
                                                {user.roles.length === 0 && <span className="text-gray-400 italic">No roles</span>}
                                            </div>
                                        </td>
                                        <td className="px-6 py-4">
                                            {user.is_active ? (
                                                <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-50 text-green-700 border border-green-200">
                                                    Active
                                                </span>
                                            ) : (
                                                <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
                                                    Inactive
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            <Button variant="ghost" size="sm" onClick={() => handleEditClick(user)}>
                                                <Edit2 className="w-4 h-4 mr-1" /> Edit
                                            </Button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>

            {/* Edit Roles Modal */}
            {editingUser && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <Card className="w-full max-w-md shadow-xl animate-in fade-in zoom-in-95 duration-200">
                        <CardHeader className="border-b bg-gray-50/50">
                            <CardTitle className="flex items-center gap-2">
                                <Shield className="w-5 h-5 text-blue-600" />
                                Manage Roles
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="p-6 space-y-4">
                            <div>
                                <p className="text-sm text-gray-500 mb-1">User</p>
                                <p className="font-medium text-gray-900">{editingUser.email}</p>
                            </div>

                            <div className="space-y-3">
                                <p className="text-sm font-medium text-gray-700">Assign Roles</p>
                                <div className="grid gap-2">
                                    {roles.map(role => {
                                        const isSelected = selectedRoleIds.includes(role.id);
                                        return (
                                            <div
                                                key={role.id}
                                                onClick={() => handleRoleToggle(role.id)}
                                                className={`
                          flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all
                          ${isSelected
                                                        ? 'border-blue-500 bg-blue-50/50 ring-1 ring-blue-500/20'
                                                        : 'border-gray-200 hover:border-blue-300 hover:bg-gray-50'
                                                    }
                        `}
                                            >
                                                <div className={`
                          w-5 h-5 rounded border flex items-center justify-center shrink-0 mt-0.5 transition-colors
                          ${isSelected ? 'bg-blue-600 border-blue-600' : 'bg-white border-gray-300'}
                        `}>
                                                    {isSelected && <Check className="w-3.5 h-3.5 text-white" />}
                                                </div>
                                                <div>
                                                    <p className={`text-sm font-medium ${isSelected ? 'text-blue-900' : 'text-gray-900'}`}>{role.name}</p>
                                                    <p className="text-xs text-gray-500 mt-0.5">{role.description}</p>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-4 border-t mt-6">
                                <Button variant="outline" onClick={() => setEditingUser(null)} disabled={isSaving}>
                                    Cancel
                                </Button>
                                <Button onClick={handleSave} disabled={isSaving} className="bg-blue-600 hover:bg-blue-700">
                                    {isSaving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                                    Save Changes
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
            {showAddModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <Card className="w-full max-w-md shadow-xl animate-in fade-in zoom-in-95 duration-200">
                        <CardHeader className="border-b bg-gray-50/50">
                            <CardTitle className="flex items-center gap-2">
                                <div className="p-1.5 bg-blue-100 rounded-md">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-600"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><line x1="19" x2="19" y1="8" y2="14" /><line x1="16" x2="22" y1="11" y2="11" /></svg>
                                </div>
                                Add New User
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="p-6 space-y-4">
                            <div className="space-y-4">
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium text-gray-700">Full Name</label>
                                    <input
                                        type="text"
                                        value={newUser.fullName}
                                        onChange={(e) => setNewUser(prev => ({ ...prev, fullName: e.target.value }))}
                                        placeholder="e.g. Taylor Hanson"
                                        className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200"
                                    />
                                </div>
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium text-gray-700">Email Address</label>
                                    <input
                                        type="email"
                                        value={newUser.email}
                                        onChange={(e) => setNewUser(prev => ({ ...prev, email: e.target.value }))}
                                        placeholder="user@example.com"
                                        className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all border-gray-200"
                                    />
                                </div>
                            </div>

                            <div className="space-y-3 pt-2">
                                <p className="text-sm font-medium text-gray-700">Assign Initial Roles</p>
                                <div className="grid grid-cols-2 gap-2">
                                    {roles.map(role => {
                                        const isSelected = selectedAddRoleIds.includes(role.id);
                                        return (
                                            <div
                                                key={role.id}
                                                onClick={() => handleAddRoleToggle(role.id)}
                                                className={`
                                                    flex items-center gap-2 p-2 rounded border cursor-pointer transition-all text-xs
                                                    ${isSelected
                                                        ? 'border-blue-500 bg-blue-50/50'
                                                        : 'border-gray-200 hover:border-blue-300 hover:bg-gray-50'
                                                    }
                                                `}
                                            >
                                                <div className={`
                                                    w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors
                                                    ${isSelected ? 'bg-blue-600 border-blue-600' : 'bg-white border-gray-300'}
                                                `}>
                                                    {isSelected && <Check className="w-3 h-3 text-white" />}
                                                </div>
                                                <span className={isSelected ? 'text-blue-900 font-medium' : 'text-gray-700'}>
                                                    {role.name}
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-6 border-t mt-4">
                                <Button variant="outline" onClick={() => setShowAddModal(false)} disabled={isSaving}>
                                    Cancel
                                </Button>
                                <Button onClick={handleAddUser} disabled={isSaving || !newUser.email || !newUser.fullName} className="bg-primary text-white">
                                    {isSaving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                                    Create User
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
};
