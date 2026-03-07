import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

// Helper to get base URL dynamically
const getBaseURL = () => {
    const configuredUrl = (process.env.NEXT_PUBLIC_API_URL || '').trim();
    if (configuredUrl) {
        return configuredUrl;
    }

    if (typeof window !== 'undefined') {
        // Use same-origin API on remote hosts to avoid port/firewall issues in production/proxy mode.
        const hostname = window.location.hostname;
        if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '0.0.0.0') {
            return 'http://127.0.0.1:8000/api/v1';
        }
        return '/api/v1';
    }
    // Fallback for SSR
    return 'http://127.0.0.1:8000/api/v1';
};

// Create axios instance
const api = axios.create({
    baseURL: getBaseURL(),
    headers: {
        'Content-Type': 'application/json',
    },
    timeout: 30000,
});

let authRedirectInProgress = false;

// Request interceptor to add JWT token
api.interceptors.request.use(
    (config) => {
        const token = useAuthStore.getState().token;
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

// Response interceptor to handle 401 (Unauthorized) and 403 (Forbidden)
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response?.status === 401) {
            const requestUrl = error.config?.url || '';
            const isLoginRequest = requestUrl.includes('/auth/token');

            // Login endpoint은 토큰 갱신/재발급 흐름이므로 즉시 강제 로그아웃/이동하지 않음
            if (isLoginRequest) {
                return Promise.reject(error);
            }

            if (requestUrl.includes('/health')) {
                return Promise.reject(error);
            }

            // Token expired or invalid
            useAuthStore.getState().logout();
            // Optional: Redirect to login page if not already there
            if (
                typeof window !== 'undefined'
                && !window.location.pathname.includes('/login')
                && !window.location.pathname.includes('/auth/login')
                && !authRedirectInProgress
            ) {
                authRedirectInProgress = true;
                window.location.href = '/login';
                window.setTimeout(() => {
                    authRedirectInProgress = false;
                }, 1000);
            }
        } else if (error.response?.status === 403) {
            // Permission denied
            // We can use a toast library here if available, or just log it
            // For now, we'll assume a simple alert or console error as requested "toast notification"
            // Since we don't have the toast library import here, we might need to inject it or use a global event
            console.error("Access Denied: You do not have permission to perform this action.");
            // Ideally: toast.error("권한이 없습니다");
        }
        return Promise.reject(error);
    }
);

export default api;
