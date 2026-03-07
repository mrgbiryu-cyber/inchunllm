'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import api from '@/lib/axios-config';
import { Save, Bot, User, Cpu } from 'lucide-react';
import ModelSelector from '@/components/shared/ModelSelector';
import { useAuthStore } from '@/store/useAuthStore';

export default function MasterSettingsPage() {
    const router = useRouter();
    const user = useAuthStore((state) => state.user);
    const isAuthorized = user?.role === 'super_admin' || user?.role === 'tenant_admin';

    useEffect(() => {
        if (!isAuthorized) {
            router.replace('/chat');
        }
    }, [isAuthorized, router]);

    const [config, setConfig] = useState<any>({
        model: 'google/gemini-2.0-flash-001',
        provider: 'OPENROUTER',
        system_prompt: '',
        temperature: 0.7
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const response = await api.get('/master/config');
            setConfig(response.data);
        } catch (error) {
            console.error("Failed to fetch master config", error);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        try {
            setSaving(true);
            await api.post('/master/config', config);
            alert("Master settings saved!");
        } catch (error) {
            console.error("Failed to save master config", error);
            alert("Failed to save settings");
        } finally {
            setSaving(false);
        }
    };

    if (!isAuthorized) {
        return <div className="p-8 text-zinc-400">접근 권한이 없습니다. 관리자 화면으로 이동 중...</div>;
    }

    if (loading) return <div className="p-8 text-zinc-400">Loading settings...</div>;

    return (
        <div className="min-h-screen bg-zinc-950 text-zinc-200 p-8">
            <div className="max-w-4xl mx-auto">
                <div className="flex items-center justify-between mb-8">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-indigo-600 rounded-xl shadow-lg shadow-indigo-900/20">
                            <Bot size={32} className="text-white" />
                        </div>
                        <div>
                            <h1 className="text-2xl font-bold text-white">System Master Butler</h1>
                            <p className="text-zinc-400">Global configuration for the system-wide AI assistant</p>
                        </div>
                    </div>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-all shadow-lg shadow-indigo-900/20 disabled:opacity-50 font-medium"
                    >
                        <Save size={18} />
                        {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {/* Left Column: Model Settings */}
                    <div className="md:col-span-1 space-y-6">
                        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
                            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                                <Cpu size={18} className="text-indigo-400" />
                                Model Configuration
                            </h2>

                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-zinc-400 mb-2">Provider</label>
                                    <div className="grid grid-cols-2 gap-2 bg-zinc-950 p-1 rounded-lg border border-zinc-800">
                                        <button
                                            type="button"
                                            onClick={() => setConfig({ ...config, provider: 'OPENROUTER' })}
                                            className={cn(
                                                "py-1.5 text-xs font-medium rounded-md transition-all",
                                                config.provider === 'OPENROUTER' ? "bg-indigo-600 text-white shadow-sm" : "text-zinc-500 hover:text-zinc-300"
                                            )}
                                        >
                                            OpenRouter
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setConfig({ ...config, provider: 'OLLAMA' })}
                                            className={cn(
                                                "py-1.5 text-xs font-medium rounded-md transition-all",
                                                config.provider === 'OLLAMA' ? "bg-emerald-600 text-white shadow-sm" : "text-zinc-500 hover:text-zinc-300"
                                            )}
                                        >
                                            Ollama
                                        </button>
                                    </div>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-zinc-400 mb-1">Model Name</label>
                                    <ModelSelector 
                                        value={config.model} 
                                        onChange={(val) => setConfig({ ...config, model: val })}
                                        provider={config.provider}
                                    />
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-zinc-400 mb-1">Temperature ({config.temperature})</label>
                                    <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.1"
                                        value={config.temperature}
                                        onChange={(e) => setConfig({ ...config, temperature: parseFloat(e.target.value) })}
                                        className="w-full accent-indigo-500"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Right Column: Persona */}
                    <div className="md:col-span-2">
                        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 h-full flex flex-col">
                            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                                <User size={18} className="text-indigo-400" />
                                Persona & System Prompt
                            </h2>
                            <p className="text-sm text-zinc-500 mb-4">
                                Define how the Master Agent should behave, what capabilities it has, and its personality.
                            </p>

                            <textarea
                                value={config.system_prompt}
                                onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
                                className="flex-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg p-4 text-sm font-mono leading-relaxed focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none resize-none"
                                placeholder="You are the Master Agent..."
                            />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

function cn(...inputs: any[]) {
    return inputs.filter(Boolean).join(' ');
}
