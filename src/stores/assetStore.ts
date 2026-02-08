import { create } from 'zustand';
import type { DesignPattern, DesignPatternComment } from '../types';
import { listGitHubTemplates } from '../services/api';

interface AssetStore {
  designPatterns: DesignPattern[];
  isLoading: boolean;
  error: string | null;
  fetchDesignPatterns: () => Promise<void>;
  addDesignPattern: (pattern: Omit<DesignPattern, 'id' | 'createdAt' | 'updatedAt' | 'viewCount' | 'comments'>) => Promise<void>;
  addComment: (patternId: string, comment: Omit<DesignPatternComment, 'id' | 'createdAt' | 'updatedAt'>) => Promise<void>;
  incrementViewCount: (patternId: string) => void;
}

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const useAssetStore = create<AssetStore>((set) => ({
  designPatterns: [],
  isLoading: false,
  error: null,

  fetchDesignPatterns: async () => {
    set({ isLoading: true, error: null });
    try {
      const githubTemplates = await listGitHubTemplates();

      const mappedPatterns: DesignPattern[] = githubTemplates.map(template => ({
        id: template.id.toString(),
        title: template.name.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' '),
        description: template.description,
        author: template.owner,
        authorEmail: '', // Not provided by public API easily
        team: 'GitHub',
        tags: template.tags,
        githubUrl: template.url,
        assetLinks: [
          { id: `link-${template.id}`, type: 'github', label: 'Repository', url: template.url }
        ],
        comments: [], // Comments would need a separate DB table, keeping empty for now
        createdAt: template.created_at,
        updatedAt: template.updated_at,
        viewCount: 0 // View count would need a separate DB table
      }));

      set({ designPatterns: mappedPatterns, isLoading: false });
    } catch (err) {
      console.error('Failed to fetch design patterns:', err);
      set({ error: 'Failed to load templates from GitHub', isLoading: false });
    }
  },

  addDesignPattern: async (pattern) => {
    // This would now need to create a repo or save metadata somewhere
    // For now, we'll just local update
    const newPattern: DesignPattern = {
      ...pattern,
      id: `pattern-${Date.now()}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      viewCount: 0,
      comments: [],
    };
    set((state) => ({
      designPatterns: [newPattern, ...state.designPatterns],
    }));
  },

  addComment: async (patternId, comment) => {
    await delay(500);
    const newComment: DesignPatternComment = {
      ...comment,
      id: `comment-${Date.now()}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    set((state) => ({
      designPatterns: state.designPatterns.map((pattern) =>
        pattern.id === patternId
          ? {
            ...pattern,
            comments: [...pattern.comments, newComment],
            updatedAt: new Date().toISOString(),
          }
          : pattern
      ),
    }));
  },

  incrementViewCount: (patternId) => {
    set((state) => ({
      designPatterns: state.designPatterns.map((pattern) =>
        pattern.id === patternId
          ? { ...pattern, viewCount: pattern.viewCount + 1 }
          : pattern
      ),
    }));
  },
}));

