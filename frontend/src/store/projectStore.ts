import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { Project, ProjectAgentConfig } from '@/types/project';

interface ProjectState {
    currentProjectId: string | null;
    currentThreadId: string | null;
    projects: Project[];
    currentConfig: ProjectAgentConfig | null;
    setCurrentProjectId: (id: string | null) => void;
    setCurrentThreadId: (id: string | null) => void;
    setProjects: (projects: Project[]) => void;
    setCurrentConfig: (config: ProjectAgentConfig | null) => void;
    getCurrentProject: () => Project | undefined;
}

export const useProjectStore = create<ProjectState>()(
    persist(
        (set, get) => ({
            currentProjectId: null,
            currentThreadId: null,
            projects: [],
            currentConfig: null,
            setCurrentProjectId: (id) => set({ currentProjectId: id }),
            setCurrentThreadId: (id) => set({ currentThreadId: id }),
            setProjects: (projects) => set({ projects }),
            setCurrentConfig: (config) => set({ currentConfig: config }),
            getCurrentProject: () => {
                const { projects, currentProjectId } = get();
                return projects.find((p) => p.id === currentProjectId);
            },
        }),
        {
            name: 'project-storage',
            partialize: (state) => ({ currentProjectId: state.currentProjectId }),
        }
    )
);
