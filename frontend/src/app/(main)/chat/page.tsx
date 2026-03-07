'use client';

import MainLayout from '@/components/shared/layout/MainLayout';
import KnowledgeGraph from '@/components/graph/KnowledgeGraph';
import LangGraphView from '@/components/langgraph/LangGraphView';
import VectorMapView from '@/components/vectormap/VectorMapView';
import ChatInterface from '@/components/chat/ChatInterface';
import { useSearchParams } from 'next/navigation';
import { useState, useEffect, Suspense } from 'react';
import { useAuthStore } from '@/store/useAuthStore';

function ChatContent() {
    const searchParams = useSearchParams();
    const [activeTab, setActiveTab] = useState('chat');
    const projectId = searchParams.get('projectId') || undefined;
    const threadId = searchParams.get('threadId') || undefined;
    const requestId = searchParams.get('request_id') || undefined; // [v4.2] Extract request_id
    const user = useAuthStore((state) => state.user);
    const isStandardUser = user?.role === 'standard_user';
    const isAdmin = user?.role === 'super_admin' || user?.role === 'tenant_admin';

    useEffect(() => {
        const tab = searchParams.get('tab');
        if (!isStandardUser && tab) {
            setActiveTab(tab);
        } else {
            setActiveTab('chat');
        }
    }, [searchParams, isStandardUser]);

    return (
        <div className="flex flex-col h-full relative bg-black text-white">
            {/* Content Area */}
            <div className="flex-1 overflow-hidden relative">
                {/* GRAPH VIEW (admin only) */}
                {isAdmin && (
                    <div className={`absolute inset-0 transition-opacity duration-300 ${activeTab === 'graph' ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'}`}>
                        <KnowledgeGraph projectId={projectId} requestId={requestId} />
                    </div>
                )}

                {/* LANGGRAPH VIEW (admin only) */}
                {isAdmin && (
                    <div className={`absolute inset-0 transition-opacity duration-300 ${activeTab === 'langgraph' ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'}`}>
                        <LangGraphView projectId={projectId} />
                    </div>
                )}

                {/* VECTOR MAP VIEW (admin only) */}
                {isAdmin && (
                    <div className={`absolute inset-0 transition-opacity duration-300 ${activeTab === 'vector' ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'}`}>
                        <VectorMapView requestId={requestId} projectId={projectId} />
                    </div>
                )}

                {/* CHAT VIEW */}
                <div className={`absolute inset-0 flex flex-col transition-opacity duration-300 ${activeTab === 'chat' ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'}`}>
                    <ChatInterface
                        key={`${projectId || 'no-project'}::${threadId || 'default'}`}
                        projectId={projectId}
                        threadId={threadId}
                    />
                </div>
            </div>
        </div>
    );
}

export default function ChatPage() {
    return (
        <Suspense fallback={<div className="flex h-full items-center justify-center bg-black text-zinc-500">Loading chat...</div>}>
            <MainLayout>
                <ChatContent />
            </MainLayout>
        </Suspense>
    );
}
