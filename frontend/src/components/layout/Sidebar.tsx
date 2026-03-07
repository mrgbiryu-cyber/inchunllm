'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useProjectStore } from '@/store/projectStore';
import { useAuthStore } from '@/store/useAuthStore';
import api from '@/lib/axios-config';
import { Folder, Shield, Menu, X, LogOut, Database, MessageSquarePlus, Hash, Trash2 } from 'lucide-react';
import clsx from 'clsx';

export default function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const searchParams = useSearchParams();
    const { currentProjectId, setCurrentProjectId, projects, setProjects } = useProjectStore();
    const { logout, user } = useAuthStore();
    const [loading, setLoading] = useState(false);
    const [isOpen, setIsOpen] = useState(false); // Mobile state
    const [threads, setThreads] = useState<any[]>([]);
    const accountLabel = user?.role === 'super_admin' || user?.role === 'tenant_admin'
        ? '관리자 계정'
        : '일반 계정';
    const isAdmin = user?.role === 'super_admin' || user?.role === 'tenant_admin';
    const showNewChatButton = true;
    const showProjectNavigation = isAdmin;
    const resolveProjectId = (projectId: string | null | undefined) => {
        if (!projectId) return null;
        return !isAdmin && projectId === 'system-master' ? null : projectId;
    };
    const effectiveCurrentProjectId = resolveProjectId(currentProjectId);
    const normalizeThreadTitle = (thread: any) => {
        const baseTitle = thread?.title?.trim();
        if (baseTitle) {
            return baseTitle;
        }
        const userPrefix = user?.username || 'user';
        const shortId = (thread?.thread_id || '').slice(0, 8);
        return `${userPrefix}_talk_${shortId || 'session'}`;
    };
    const dedupeAndSortThreads = (items: any[]) => {
        const map = new Map<string, any>();
        items.forEach((thread) => {
            if (!thread?.thread_id) return;
            if (!map.has(thread.thread_id)) {
                map.set(thread.thread_id, thread);
            }
        });
        return Array.from(map.values()).sort((a, b) => {
            const at = new Date(a.updated_at || 0).getTime();
            const bt = new Date(b.updated_at || 0).getTime();
            return bt - at;
        });
    };
    const isDefaultThread = (thread: any) => {
        if (thread?.is_default === true) {
            return true;
        }
        const title = (thread?.title || '').trim();
        return title === '기본 상담방';
    };
    const refreshThreads = async (activeProjectId: string) => {
        const threadsRes = await api.get(`/projects/${activeProjectId}/threads`);
        const list = dedupeAndSortThreads(Array.isArray(threadsRes.data) ? threadsRes.data : []);
        setThreads(list);
        return list;
    };

    useEffect(() => {
        if (!isAdmin && currentProjectId === 'system-master') {
            setCurrentProjectId(null);
        }
    }, [isAdmin, currentProjectId, setCurrentProjectId]);

    useEffect(() => {
        if (!isAdmin && !loading && !currentProjectId && projects.length > 0) {
            const firstUsableProject = projects.find(p => p.id !== 'system-master');
            setCurrentProjectId(firstUsableProject ? firstUsableProject.id : null);
        }
    }, [isAdmin, projects, currentProjectId, loading, setCurrentProjectId]);

    useEffect(() => {
        const safeProjectId = resolveProjectId(currentProjectId);
        if (!safeProjectId) {
            setThreads([]);
            return;
        }
        if (safeProjectId) {
            // Fetch threads for project
            api.get(`/projects/${safeProjectId}/threads`)
                .then(res => setThreads(dedupeAndSortThreads(Array.isArray(res.data) ? res.data : [])))
                .catch(err => {
                    console.error("Failed to fetch threads", err);
                    setThreads([]);
                });
        }
    }, [currentProjectId, isAdmin]);

    useEffect(() => {
        fetchProjects();
    }, []);

    const fetchProjects = async () => {
        try {
            setLoading(true);
            const response = await api.get('/projects/');
            setProjects(response.data);
        } catch (error) {
            console.error("Failed to fetch projects in sidebar", error);
        } finally {
            setLoading(false);
        }
    };

    const handleNewChat = async () => {
        setIsOpen(false);
        let activeProjectId = effectiveCurrentProjectId;

        if (!activeProjectId) {
            try {
                const fallbackProject = projects.find(p => p.id !== 'system-master');
                if (projects.length === 0 || !fallbackProject) {
                    const seedName = `${user?.username || 'user'}_talk`;
                    const response = await api.post('/projects/', {
                        name: seedName,
                        project_type: 'GROWTH_SUPPORT'
                    });
                    setProjects([...projects, response.data]);
                    activeProjectId = response.data.id;
                    setCurrentProjectId(activeProjectId);
                } else {
                    activeProjectId = fallbackProject.id;
                    setCurrentProjectId(activeProjectId);
                }
            } catch (initErr) {
                console.error("Failed to initialize default project", initErr);
                alert("상담 프로젝트 준비에 실패했습니다.");
                return;
            }
        }

        if (!activeProjectId) {
            alert("상담 컨텍스트를 준비하지 못했습니다.");
            return;
        }

        // [v5.0] New Chat: Prompt for Name
        const chatName = window.prompt("새 상담명을 입력하세요");
        if (chatName === null) return; // Cancelled
        
        try {
            // Create Thread API Call
            const threadTitle = chatName || `${user?.username || 'user'}_talk`;
            const response = await api.post(`/projects/${activeProjectId}/threads`, { title: threadTitle });
            const { thread_id } = response.data;
            
            const list = await refreshThreads(activeProjectId);
            
            // Redirect to new thread
            setCurrentProjectId(activeProjectId);
            router.push(`/chat?projectId=${activeProjectId}&threadId=${thread_id}`);
        } catch (err) {
            console.error("Failed to create thread", err);
            alert("대화방 생성에 실패했습니다.");
        }
    };
    const handleDeleteThread = async (thread: any) => {
        const threadId = thread?.thread_id;
        const threadTitle = normalizeThreadTitle(thread);
        if (!threadId || !effectiveCurrentProjectId) return;
        if (isDefaultThread(thread)) {
            alert("기본 상담방은 삭제할 수 없습니다.");
            return;
        }
        const ok = window.confirm(
            `정말 삭제하시겠습니까?\n"${threadTitle}"\n삭제되면 다시 복원하기 어렵습니다.`
        );
        if (!ok) return;

        try {
            await api.delete(`/projects/${effectiveCurrentProjectId}/threads/${threadId}`);
            const list = await refreshThreads(effectiveCurrentProjectId);

            const currentThreadId = searchParams.get('threadId');
            if (currentThreadId === threadId) {
                if (list.length > 0) {
                    router.push(`/chat?projectId=${effectiveCurrentProjectId}&threadId=${list[0].thread_id}`);
                } else {
                    router.push(`/chat?projectId=${effectiveCurrentProjectId}`);
                }
            }
        } catch (err: any) {
            if (err?.response?.data?.error_code === 'THREAD_DELETE_BLOCKED') {
                alert("기본 상담방은 삭제할 수 없습니다.");
                return;
            }
            if (err?.response?.status === 404) {
                alert("이미 삭제되었거나 찾을 수 없는 상담방입니다.");
                await refreshThreads(effectiveCurrentProjectId);
                return;
            }
            console.error("Failed to delete thread", err);
            alert("상담방 삭제에 실패했습니다.");
        }
    };

    const isActive = (path: string) => {
        return pathname.startsWith(path) ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-white';
    };

    const currentTab = searchParams.get('tab');

    return (
        <>
            {/* Mobile Toggle Button */}
            <button 
                onClick={() => setIsOpen(!isOpen)}
                className="fixed top-4 left-4 z-[60] p-2 bg-zinc-900 border border-zinc-800 rounded-lg text-white lg:hidden shadow-xl"
            >
                {isOpen ? <X size={20} /> : <Menu size={20} />}
            </button>

            {/* Backdrop */}
            {isOpen && (
                <div 
                    className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[50] lg:hidden"
                    onClick={() => setIsOpen(false)}
                />
            )}

            <div className={clsx(
                "fixed inset-y-0 left-0 z-[55] w-64 bg-zinc-950 border-r border-zinc-800 flex flex-col transition-transform duration-300 lg:static lg:translate-x-0",
                isOpen ? "translate-x-0" : "-translate-x-full"
            )}>
                <div className="p-6 pb-2">
                    {/* [v5.0] Logo Link Removed (Pointer Events None) */}
                    <div className="flex items-center gap-2 group mb-6 pointer-events-none select-none">
                        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-900/20 group-hover:scale-105 transition-transform">
                            <span className="text-white font-bold text-xs">B</span>
                        </div>
                        <h1 className="text-lg font-bold text-white tracking-tight">AI BizPlan</h1>
                    </div>

                    {showNewChatButton && (
                        <button
                            onClick={handleNewChat}
                            className="w-full flex items-center justify-center gap-2 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-all font-bold text-sm shadow-lg shadow-indigo-900/20 mb-2 group"
                        >
                            <MessageSquarePlus size={18} className="group-hover:scale-110 transition-transform"/>
                            <span>새 상담</span>
                        </button>
                    )}
                </div>

                    <div className="flex-1 px-4 space-y-6 overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800">
                    {/* Main Navigation */}
                    {showProjectNavigation && (
                        <nav className="space-y-1">
                            <Link
                                href="/projects"
                                onClick={() => setIsOpen(false)}
                                className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-all text-sm font-medium ${isActive('/projects') && !pathname.includes('master-settings') ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-white'}`}
                            >
                                <Folder size={18} />
                                상담
                            </Link>
                        </nav>
                    )}

                    {/* System Links Section (Replacing Active Projects) */}
                            {effectiveCurrentProjectId && (
                        <div className="mb-6">
                            <div className="px-3 mb-2 flex items-center justify-between">
                                <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">상담 세션</span>
                            </div>
                            <div className="space-y-1">
                        {threads.map(thread => {
                                    const isCurrentThread = searchParams.get('threadId') === thread.thread_id;
                                    const disableDelete = isDefaultThread(thread);
                                    return (
                                        <div
                                            key={thread.thread_id}
                                            className={clsx(
                                                "group relative flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all",
                                                isCurrentThread
                                                    ? 'bg-zinc-800 text-white font-bold shadow-md border-l-2 border-indigo-500'
                                                    : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
                                            )}
                                        >
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    const nextProjectId = encodeURIComponent(effectiveCurrentProjectId);
                                                    const nextThreadId = encodeURIComponent(thread.thread_id);
                                                    router.push(`/chat?projectId=${nextProjectId}&threadId=${nextThreadId}`);
                                                }}
                                                className="flex-1 min-w-0 flex items-center gap-2 text-left"
                                                title={normalizeThreadTitle(thread)}
                                            >
                                                <div className={clsx(
                                                    "p-1.5 rounded-md transition-colors flex-shrink-0",
                                                    isCurrentThread ? "bg-indigo-500/20 text-indigo-400" : "bg-zinc-800/50 text-zinc-500 group-hover:text-zinc-300"
                                                )}>
                                                    <Hash size={14} />
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <span className="block truncate font-medium">{normalizeThreadTitle(thread)}</span>
                                                    <span className={clsx(
                                                        "block text-[10px] truncate mt-0.5",
                                                        isCurrentThread ? "text-zinc-400" : "text-zinc-600"
                                                    )}>
                                                        {new Date(thread.updated_at).toLocaleDateString()}
                                                    </span>
                                                </div>
                                            </button>
                                            {!disableDelete && (
                                            <button
                                                    type="button"
                                                    onClick={(event) => {
                                                        event.preventDefault();
                                                        event.stopPropagation();
                                                        handleDeleteThread(thread);
                                                    }}
                                                    title={`"${normalizeThreadTitle(thread)}" 삭제`}
                                                    aria-label={`${normalizeThreadTitle(thread)} 삭제`}
                                                    className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-all"
                                                >
                                                    <Trash2 size={14} />
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}
                                {threads.length === 0 && (
                                    <div className="px-3 py-2 text-xs text-zinc-600 italic">
                                        진행중인 상담방이 없습니다. 대화 시작 시 자동 생성됩니다.
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* System Section */}
                    {user?.role === 'super_admin' && (
                    <div>
                        <div className="px-3 mb-2">
                            <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">System</span>
                        </div>
                        <nav className="space-y-1">
                            <Link
                                href="/master-settings"
                                onClick={() => setIsOpen(false)}
                                className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-all text-sm font-medium ${isActive('/master-settings')}`}
                            >
                                <Shield size={18} />
                                Master Butler
                            </Link>
                            <Link
                                href="/admin/rules"
                                onClick={() => setIsOpen(false)}
                                className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-all text-sm font-medium ${isActive('/admin/rules')}`}
                            >
                                <Database size={18} />
                                Rule Tuning
                            </Link>
                        </nav>
                    </div>
                    )}
                </div>

                    <div className="p-4 mt-auto border-t border-zinc-800/50 space-y-2">
                    <button 
                        onClick={() => {
                            logout();
                            router.push('/login');
                        }}
                        className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-all text-sm font-medium"
                    >
                        <LogOut size={18} />
                        Logout
                    </button>

                    <div className="flex items-center gap-3 p-2 rounded-xl bg-zinc-900/50 border border-zinc-800/50">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-tr from-zinc-800 to-zinc-700 flex items-center justify-center">
                            <span className="text-zinc-400 font-bold">U</span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="text-xs font-bold text-white truncate">{accountLabel}</div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
}
