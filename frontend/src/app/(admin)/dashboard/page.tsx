'use client';

import { useState, Suspense } from 'react';
import MainLayout from '@/components/shared/layout/MainLayout';
import { useDomainStore } from '@/store/useDomainStore';
import Editor from '@monaco-editor/react';
import { Plus, Trash2, Save, RefreshCw } from 'lucide-react';

function DashboardContent() {
    const { domains, addDomain, setCurrentDomain } = useDomainStore();
    const [newDomainName, setNewDomainName] = useState('');
    const [newRepoRoot, setNewRepoRoot] = useState('');
    const [activeTab, setActiveTab] = useState('domains'); // 'domains' | 'agents' | 'quota'

    // Mock Agent Config
    const [agentConfig, setAgentConfig] = useState(`# Agent Configuration
agents:
  - name: "developer"
    model: "claude-3-5-sonnet-20240620"
    temperature: 0.7
    system_prompt: "You are an expert developer..."
  
  - name: "planner"
    model: "google/gemini-2.0-flash-001"
    temperature: 0.2
`);

    const handleAddDomain = () => {
        if (!newDomainName || !newRepoRoot) return;
        const newDomain = {
            id: Date.now().toString(),
            name: newDomainName,
            repo_root: newRepoRoot
        };
        addDomain(newDomain);
        setNewDomainName('');
        setNewRepoRoot('');
    };

    const handleSaveConfig = () => {
        // In real app: POST /api/v1/admin/config
        alert('Configuration saved successfully!');
    };

    return (
        <div className="flex flex-col h-full bg-zinc-950 p-6 space-y-6 overflow-y-auto">
            <h1 className="text-2xl font-bold text-white mb-4">Super Admin Dashboard</h1>

            {/* Tabs */}
            <div className="flex gap-4 border-b border-zinc-800 pb-2">
                {['domains', 'agents', 'quota'].map((tab) => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-4 py-2 text-sm font-medium rounded-md capitalize ${activeTab === tab
                                ? 'bg-blue-600 text-white'
                                : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
                            }`}
                    >
                        {tab} Management
                    </button>
                ))}
            </div>

            {/* DOMAIN MANAGEMENT */}
            {activeTab === 'domains' && (
                <div className="space-y-6">
                    <div className="bg-zinc-900 p-6 rounded-lg border border-zinc-800">
                        <h2 className="text-lg font-semibold text-zinc-200 mb-4">Add New Domain</h2>
                        <div className="flex gap-4 items-end">
                            <div className="flex-1">
                                <label className="block text-xs text-zinc-500 mb-1">Project Name</label>
                                <input
                                    value={newDomainName}
                                    onChange={(e) => setNewDomainName(e.target.value)}
                                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none"
                                    placeholder="e.g. My New Project"
                                />
                            </div>
                            <div className="flex-1">
                                <label className="block text-xs text-zinc-500 mb-1">Repository Root Path</label>
                                <input
                                    value={newRepoRoot}
                                    onChange={(e) => setNewRepoRoot(e.target.value)}
                                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none"
                                    placeholder="e.g. /home/user/projects/new-app"
                                />
                            </div>
                            <button
                                onClick={handleAddDomain}
                                className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded flex items-center gap-2 text-sm font-medium"
                            >
                                <Plus size={16} /> Add
                            </button>
                        </div>
                    </div>

                    <div className="bg-zinc-900 p-6 rounded-lg border border-zinc-800">
                        <h2 className="text-lg font-semibold text-zinc-200 mb-4">Active Domains</h2>
                        <div className="space-y-2">
                            {domains.map((domain) => (
                                <div key={domain.id} className="flex items-center justify-between p-3 bg-zinc-950 rounded border border-zinc-800">
                                    <div>
                                        <div className="font-medium text-zinc-200">{domain.name}</div>
                                        <div className="text-xs text-zinc-500 font-mono">{domain.repo_root}</div>
                                    </div>
                                    <div className="flex gap-2">
                                        <button
                                            onClick={() => setCurrentDomain(domain)}
                                            className="text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 px-3 py-1 rounded"
                                        >
                                            Switch To
                                        </button>
                                        <button className="text-red-500 hover:bg-red-900/20 p-1 rounded">
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* AGENT CONFIG */}
            {activeTab === 'agents' && (
                <div className="flex-1 flex flex-col bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden">
                    <div className="flex items-center justify-between p-4 border-b border-zinc-800">
                        <h2 className="text-lg font-semibold text-zinc-200">agents.yaml Configuration</h2>
                        <div className="flex gap-2">
                            <button className="text-zinc-400 hover:text-white p-2 rounded hover:bg-zinc-800">
                                <RefreshCw size={18} />
                            </button>
                            <button
                                onClick={handleSaveConfig}
                                className="bg-green-600 hover:bg-green-500 text-white px-4 py-1.5 rounded text-sm font-medium flex items-center gap-2"
                            >
                                <Save size={16} /> Save Changes
                            </button>
                        </div>
                    </div>
                    <div className="flex-1">
                        <Editor
                            height="100%"
                            defaultLanguage="yaml"
                            theme="vs-dark"
                            value={agentConfig}
                            onChange={(value) => setAgentConfig(value || '')}
                            options={{
                                minimap: { enabled: false },
                                fontSize: 14,
                                scrollBeyondLastLine: false,
                            }}
                        />
                    </div>
                </div>
            )}

            {/* QUOTA MANAGEMENT */}
            {activeTab === 'quota' && (
                <div className="bg-zinc-900 p-6 rounded-lg border border-zinc-800">
                    <h2 className="text-lg font-semibold text-zinc-200 mb-4">User Quotas</h2>
                    <div className="text-zinc-500 text-sm">
                        Quota management interface is under construction.
                    </div>
                </div>
            )}
        </div>
    );
}

export default function AdminDashboard() {
    return (
        <Suspense fallback={<div className="p-6 text-zinc-500">Loading dashboard...</div>}>
            <MainLayout>
                <DashboardContent />
            </MainLayout>
        </Suspense>
    );
}
