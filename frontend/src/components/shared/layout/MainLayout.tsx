'use client';

import { useState, useEffect } from 'react';
import {
    MessageSquare,
    Share2,
    Workflow,
    Map,
    Activity,
    Settings,
    LogOut,
    Plus,
    Search,
    Menu,
    X,
    Cpu,
    StopCircle,
    CheckCircle2,
    AlertCircle,
    Folder,
    Zap
} from 'lucide-react';
import { useAuthStore } from '@/store/useAuthStore';
import { useRouter, useParams, usePathname, useSearchParams } from 'next/navigation';
import clsx from 'clsx';
import { useWorker } from '@/hooks/useWorker';
import Link from 'next/link';
import api from '@/lib/axios-config';
import { Project } from '@/types/project';
import Sidebar from '@/components/layout/Sidebar';
import { useProjectStore } from '@/store/projectStore';
import LogConsole from '@/components/chat/LogConsole';

interface MainLayoutProps {
    children: React.ReactNode;
}

export default function MainLayout({ children }: MainLayoutProps) {
    const params = useParams();
    const pathname = usePathname();
    const router = useRouter();
    const searchParams = useSearchParams();
    const { user, token, isAuthenticated, isHydrated, hydrateFromToken } = useAuthStore();
    const [mounted, setMounted] = useState(false);
    const { projects, currentProjectId, setCurrentProjectId } = useProjectStore();
    const [isLogsOpen, setIsLogsOpen] = useState(false);
    const isAdmin = user?.role === 'super_admin' || user?.role === 'tenant_admin';
    const resolveProjectId = (projectId: string | null | undefined) => {
        if (!projectId) return null;
        return (!isAdmin && projectId === 'system-master') ? null : projectId;
    };
    const firstUsableProjectId = projects.find(project => isAdmin || project.id !== 'system-master')?.id || null;

    // Derive effective projectId from either URL path or search params
    const requestedProjectId = (params.projectId as string) || searchParams.get('projectId') || null;
    const sanitizedRequestedProjectId = resolveProjectId(requestedProjectId);
    const projectId = resolveProjectId(sanitizedRequestedProjectId || resolveProjectId(currentProjectId));

    // Handle hydration
    useEffect(() => {
        setMounted(true);
        hydrateFromToken();
    }, []);

    // Redirect non-authenticated users out of authenticated shell
    useEffect(() => {
        if (!mounted || !isHydrated) {
            return;
        }

        if (pathname === '/login' || pathname === '/auth/login') {
            return;
        }

        if (!isAuthenticated || !token || !user) {
            router.replace('/login');
        }
    }, [mounted, isHydrated, pathname, isAuthenticated, token, user, router]);

    // Sync store with derived projectId
    useEffect(() => {
        if (!isAdmin) {
            const invalidRequested = requestedProjectId === 'system-master';
            const invalidCurrent = currentProjectId === 'system-master';
            
            if (invalidRequested || invalidCurrent) {
                setCurrentProjectId(null);
                router.replace('/chat');
                return;
            }
        }

        if (projectId) {
            setCurrentProjectId(projectId);
        } else if (!isAdmin && !projectId && firstUsableProjectId) {
            setCurrentProjectId(firstUsableProjectId);
        } else {
            setCurrentProjectId(null);
        }
    }, [requestedProjectId, projectId, currentProjectId, isAdmin, router, firstUsableProjectId]);

    const handleProjectChange = (id: string) => {
        setCurrentProjectId(id);
        const tab = searchParams.get('tab') || 'chat';
        
        if (id === 'system-master') {
            router.push(`/chat?tab=${tab}`);
        } else {
            router.push(`/chat?projectId=${id}&tab=${tab}`);
        }
    };

    const handleTabChange = (tabId: string) => {
        const pId = isAdmin ? (searchParams.get('projectId') || projectId) : (firstUsableProjectId || searchParams.get('projectId') || projectId);
        
        if (!pId || pId === 'system-master') {
            router.push(`/chat?tab=${tabId}`);
        } else {
            router.push(`/chat?projectId=${pId}&tab=${tabId}`);
        }
    };

    return (
        <div className="flex h-screen w-full bg-zinc-950 text-zinc-50 overflow-hidden">
            {/* Unified Sidebar component which handles mobile responsive */}
            <Sidebar />

            {/* CENTER PANEL (Main Content) */}
            <main className="flex flex-1 flex-col min-w-0 bg-zinc-950 relative">
                {/* Top Navigation: ACTIVE PROJECTS Tabs */}
                {isAdmin && (
                    <header className="flex h-14 items-center justify-between border-b border-zinc-800 px-4 bg-zinc-950/50 backdrop-blur-md z-40">
                        <div className="flex-1 flex justify-center overflow-hidden">
                            <div className="flex items-center gap-2 overflow-x-auto no-scrollbar max-w-full px-12 lg:px-0 cursor-grab active:cursor-grabbing select-none scroll-smooth">
                                {/* System Master Tab */}
                                <button
                                    onClick={() => handleProjectChange('system-master')}
                                    className={clsx(
                                        "flex-shrink-0 flex items-center gap-2 rounded-full px-4 py-1.5 text-xs font-bold transition-all whitespace-nowrap border",
                                        (!projectId || projectId === 'system-master')
                                            ? "bg-indigo-600/20 border-indigo-500 text-indigo-400 shadow-[0_0_15px_rgba(99,102,241,0.2)]"
                                            : "border-zinc-800 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700"
                                    )}
                                >
                                    <Activity size={14} />
                                    SYSTEM MASTER
                                </button>

                                <div className="flex-shrink-0 w-px h-4 bg-zinc-800 mx-1"></div>

                                {/* Project Tabs */}
                                {projects.map((project) => (
                                    <button
                                        key={project.id}
                                        onClick={() => handleProjectChange(project.id)}
                                        className={clsx(
                                            "flex-shrink-0 flex items-center gap-2 rounded-full px-4 py-1.5 text-xs font-medium transition-all whitespace-nowrap border",
                                            (projectId === project.id)
                                                ? "bg-emerald-600/20 border-emerald-500 text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.2)]"
                                                : "border-zinc-800 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700"
                                        )}
                                    >
                                        <Folder size={14} />
                                        {project.name}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </header>
                )}

                {/* Sub-navigation for selected project views (Chat, Graph, etc.) */}
                {isAdmin && (
                    <div className="flex h-10 items-center justify-center border-b border-zinc-900/50 bg-zinc-950/30 px-4 z-30">
                        <div className="flex items-center gap-6">
                            {[
                                { id: 'chat', label: 'Chat', icon: MessageSquare },
                                { id: 'graph', label: 'Graph', icon: Share2 },
                                { id: 'langgraph', label: 'Flow', icon: Workflow },
                                { id: 'vector', label: 'Vector', icon: Map },
                            ].map((view) => {
                                const currentTab = searchParams.get('tab');
                                const isActive = currentTab === view.id || (!currentTab && view.id === 'chat');
                                return (
                                    <button
                                        key={view.id}
                                        onClick={() => handleTabChange(view.id)}
                                        className={clsx(
                                            "flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest transition-colors",
                                            isActive ? "text-white" : "text-zinc-600 hover:text-zinc-400"
                                        )}
                                    >
                                        <view.icon size={12} className={isActive ? "text-indigo-500" : ""} />
                                        {view.label}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Content Area */}
                <div className="flex-1 overflow-hidden relative">
                    {children}
                </div>
            </main>

            {/* Persistent Log Console */}
            <LogConsole 
                isOpen={isLogsOpen} 
                onClose={() => setIsLogsOpen(false)} 
                logs={[]} // It will subscribe to project logs internally or show global logs
            />
        </div>
    );
}
