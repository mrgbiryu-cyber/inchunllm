'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/store/useAuthStore';
import api from '@/lib/axios-config';
import { Loader2 } from 'lucide-react';

type JwtPayload = {
    sub?: string;
    username?: string;
    tenant_id?: string;
    role?: string;
};

const decodeJwtPayload = (token: string): JwtPayload | null => {
    try {
        const tokenPayload = token.split('.')[1];
        if (!tokenPayload) return null;

        const padded = tokenPayload
            .replace(/-/g, '+')
            .replace(/_/g, '/')
            .padEnd(tokenPayload.length + (4 - (tokenPayload.length % 4)) % 4, '=');

        const json = typeof window === 'undefined' ? null : atob(padded);
        return json ? JSON.parse(json) : null;
    } catch (error) {
        return null;
    }
};

export default function LoginPage() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const router = useRouter();
    const login = useAuthStore((state) => state.login);
    const allowedRoles = ['super_admin', 'tenant_admin', 'standard_user'];

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setLoading(true);
        setError('');

        const formData = new FormData(e.currentTarget);
        const rawUsername = String(formData.get('username') || '');
        const rawPassword = String(formData.get('password') || '');
        const normalizedUsername = rawUsername.trim();
        const normalizedPassword = rawPassword.trim();
        setUsername(normalizedUsername);
        setPassword(normalizedPassword);

        try {
            const response = await api.post('/auth/token', {
                username: normalizedUsername,
                password: normalizedPassword,
            });

            if (normalizedUsername !== username) {
                console.log('[LOGIN] normalized username changed', {
                    from: username,
                    to: normalizedUsername,
                    length: normalizedUsername.length,
                });
            }

            const { access_token } = response.data;
            const payload = decodeJwtPayload(access_token);
            if (!payload) {
                setError('로그인 토큰을 해석할 수 없습니다.');
                return;
            }

            const role = (payload.role || 'standard_user').trim().toLowerCase();
            if (!allowedRoles.includes(role)) {
                console.error('[LOGIN] Invalid role in token', { role, payload });
                setError(`지원되지 않는 사용자 권한입니다. (${role || 'undefined'})`);
                return;
            }

            console.log('[LOGIN] JWT payload', {
                sub: payload.sub,
                username: payload.username || username,
                role,
                tenantId: payload.tenant_id || 'tenant_default',
            });

            login(access_token, {
                id: payload.sub || username,
                username: payload.username || username,
                role,
                tenant_id: payload.tenant_id || 'tenant_default',
            });
            router.push('/chat');
        } catch (err: any) {
            console.error('[LOGIN] request failed', {
                status: err?.response?.status,
                data: err?.response?.data,
                message: err?.message,
            });
            const detail = err?.response?.data;
            if (err?.response?.status === 401) {
                setError(detail?.detail || detail?.message || '아이디/비밀번호를 확인해주세요.');
                return;
            }
            setError(detail?.detail || detail?.message || 'Login failed. Please check your credentials.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-50">
            <div className="w-full max-w-md space-y-8 p-8">
                <div className="text-center">
                    <h1 className="text-3xl font-bold tracking-tight text-blue-500">AI BizPlan</h1>
                    <p className="mt-2 text-sm text-zinc-400">Sign in to your account</p>
                </div>

                <form onSubmit={handleSubmit} className="mt-8 space-y-6">
                    <div className="space-y-4">
                        <div>
                            <label htmlFor="username" className="block text-sm font-medium text-zinc-300">
                                Username
                            </label>
                            <input
                                id="username"
                                name="username"
                                type="text"
                                required
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                autoComplete="username"
                                autoCapitalize="none"
                                spellCheck={false}
                                className="mt-1 block w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-zinc-100 placeholder-zinc-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 sm:text-sm"
                                placeholder="Enter your username"
                            />
                        </div>

                        <div>
                            <label htmlFor="password" className="block text-sm font-medium text-zinc-300">
                                Password
                            </label>
                            <input
                                id="password"
                                name="password"
                                type="password"
                                required
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                autoComplete="current-password"
                                className="mt-1 block w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-zinc-100 placeholder-zinc-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 sm:text-sm"
                                placeholder="Enter your password"
                            />
                        </div>
                    </div>

                    {error && (
                        <div className="rounded-md bg-red-900/50 p-3 text-sm text-red-200">
                            {error}
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={loading}
                        className="flex w-full justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {loading ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Signing in...
                            </>
                        ) : (
                            'Sign in'
                        )}
                    </button>
                </form>
            </div>
        </div>
    );
}
