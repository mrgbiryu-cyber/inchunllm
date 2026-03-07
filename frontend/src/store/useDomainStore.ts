import { create } from 'zustand';

interface Domain {
    id: string;
    name: string;
    repo_root: string;
}

interface DomainState {
    domains: Domain[];
    currentDomain: Domain | null;
    setCurrentDomain: (domain: Domain) => void;
    addDomain: (domain: Domain) => void;
}

export const useDomainStore = create<DomainState>((set) => ({
    domains: [
        { id: 'd1', name: 'AIBizPlan Project', repo_root: '/project/smartbizplanning' },
        { id: 'd2', name: 'Blog Automation', repo_root: '/project/blog-saas' },
    ],
    currentDomain: { id: 'd1', name: 'AIBizPlan Project', repo_root: '/project/smartbizplanning' },
    setCurrentDomain: (domain) => set({ currentDomain: domain }),
    addDomain: (domain) => set((state) => ({ domains: [...state.domains, domain] })),
}));
