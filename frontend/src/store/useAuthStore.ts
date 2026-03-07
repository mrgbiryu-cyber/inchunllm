import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface User {
    id: string;
    username: string;
    role: string;
    tenant_id: string;
}

interface AuthState {
    token: string | null;
    user: User | null;
    isAuthenticated: boolean;
    isHydrated: boolean;
    login: (token: string, user: User) => void;
    logout: () => void;
    setHydrated: (value: boolean) => void;
    hydrateFromToken: () => void;
}

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

export const useAuthStore = create<AuthState>()(
    persist(
        (set, get) => ({
            token: null,
            user: null,
            isAuthenticated: false,
            isHydrated: false,
            login: (token, user) => set({ token, user, isAuthenticated: true }),
            logout: () => set({ token: null, user: null, isAuthenticated: false }),
            setHydrated: (value) => set({ isHydrated: value }),
            hydrateFromToken: () => {
                const state = get();
                const token = state.token;

                if (!token) {
                    set({ user: null, isAuthenticated: false });
                    return;
                }

                const payload = decodeJwtPayload(token);
                if (!payload) {
                    set({ user: null, isAuthenticated: false, token: null });
                    return;
                }

                const role = (payload.role || 'standard_user').trim().toLowerCase();
                if (!payload.sub || !payload.tenant_id || !role) {
                    set({ user: null, isAuthenticated: false, token: null });
                    return;
                }

                const user: User = {
                    id: payload.sub,
                    username: payload.username || payload.sub || state.user?.username || '',
                    role,
                    tenant_id: payload.tenant_id,
                };

                set({
                    user,
                    isAuthenticated: true,
                });
            },
        }),
        {
            name: 'auth-storage',
            onRehydrateStorage: () => (state) => {
                if (!state) {
                    return;
                }

                state.setHydrated(true);
                state.hydrateFromToken();
            },
        }
    )
);
