'use client';

import React, { Suspense, useEffect, useState } from 'react';
import Sidebar from '@/components/layout/Sidebar';
import { useAuthStore } from '@/store/useAuthStore';
import { useRouter } from 'next/navigation';

export default function ProjectsLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const router = useRouter();
    const user = useAuthStore((state) => state.user);
    const [isAuthorized, setIsAuthorized] = useState(false);

    useEffect(() => {
        const admin = user?.role === 'super_admin' || user?.role === 'tenant_admin';
        if (user && !admin) {
            router.replace('/chat');
            return;
        }
        if (user) {
            setIsAuthorized(true);
        }
    }, [user, router]);

    if (user && !isAuthorized) {
        return <div className="flex h-screen items-center justify-center bg-zinc-950 text-zinc-400">접근 권한이 없습니다. 상담 화면으로 이동 중...</div>;
    }

    return (
        <div className="flex h-screen bg-zinc-950 text-zinc-50 overflow-hidden">
            <Suspense fallback={<div className="w-64 bg-zinc-950 border-r border-zinc-800 flex items-center justify-center text-zinc-500">Loading menu...</div>}>
                <Sidebar />
            </Suspense>
            <main className="flex-1 overflow-auto p-4 md:p-8 pt-16 lg:pt-8">
                {children}
            </main>
        </div>
    );
}
