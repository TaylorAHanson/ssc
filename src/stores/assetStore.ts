import { create } from 'zustand';
import type { DesignPattern, DesignPatternComment } from '../types';

interface AssetStore {
  designPatterns: DesignPattern[];
  fetchDesignPatterns: () => Promise<void>;
  addDesignPattern: (pattern: Omit<DesignPattern, 'id' | 'createdAt' | 'updatedAt' | 'viewCount' | 'comments'>) => Promise<void>;
  addComment: (patternId: string, comment: Omit<DesignPatternComment, 'id' | 'createdAt' | 'updatedAt'>) => Promise<void>;
  incrementViewCount: (patternId: string) => void;
}

// Mock design patterns data
const mockDesignPatterns: DesignPattern[] = [
  {
    id: 'pattern-1',
    title: 'ETL Pipeline Template',
    description: 'A reusable ETL pipeline pattern for processing large-scale data transformations with error handling and monitoring.',
    author: 'John Smith',
    authorEmail: 'john.smith@example.com',
    team: 'Data Engineering',
    tags: ['pipeline', 'etl', 'databricks', 'spark'],
    githubUrl: 'https://github.com/example/etl-pipeline-template',
    assetLinks: [
      { id: 'link-1', type: 'confluence', label: 'Documentation', url: 'https://confluence.example.com/etl-pipeline' },
      { id: 'link-2', type: 'video', label: 'Demo Video', url: 'https://youtube.com/watch?v=demo1' },
    ],
    comments: [
      {
        id: 'comment-1',
        designPatternId: 'pattern-1',
        author: 'Jane Doe',
        authorEmail: 'jane.doe@example.com',
        content: 'Great pattern! Consider adding support for streaming data sources.',
        createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
        updatedAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
      },
    ],
    createdAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    viewCount: 145,
  },
  {
    id: 'pattern-2',
    title: 'Dashboard Visualization Framework',
    description: 'A comprehensive dashboard framework built with React and D3.js for creating interactive data visualizations.',
    author: 'Sarah Johnson',
    authorEmail: 'sarah.johnson@example.com',
    team: 'Frontend Engineering',
    tags: ['dashboard', 'visualization', 'react', 'd3'],
    githubUrl: 'https://github.com/example/dashboard-framework',
    assetLinks: [
      { id: 'link-3', type: 'confluence', label: 'Design Specs', url: 'https://confluence.example.com/dashboard-specs' },
      { id: 'link-4', type: 'video', label: 'Walkthrough', url: 'https://youtube.com/watch?v=demo2' },
      { id: 'link-5', type: 'documentation', label: 'API Docs', url: 'https://docs.example.com/dashboard' },
    ],
    comments: [],
    createdAt: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString(),
    viewCount: 89,
  },
  {
    id: 'pattern-3',
    title: 'ML Model Training Pipeline',
    description: 'End-to-end machine learning pipeline for training, validating, and deploying models with MLOps best practices.',
    author: 'Mike Chen',
    authorEmail: 'mike.chen@example.com',
    team: 'ML Engineering',
    tags: ['pipeline', 'ml', 'mlops', 'databricks'],
    githubUrl: 'https://github.com/example/ml-pipeline',
    assetLinks: [
      { id: 'link-6', type: 'confluence', label: 'MLOps Guide', url: 'https://confluence.example.com/mlops' },
    ],
    comments: [
      {
        id: 'comment-2',
        designPatternId: 'pattern-3',
        author: 'Alex Brown',
        authorEmail: 'alex.brown@example.com',
        content: 'Would love to see integration with model registry.',
        createdAt: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
        updatedAt: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
      },
      {
        id: 'comment-3',
        designPatternId: 'pattern-3',
        author: 'Mike Chen',
        authorEmail: 'mike.chen@example.com',
        content: 'Great suggestion! I\'ll add that in the next iteration.',
        createdAt: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
        updatedAt: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
      },
    ],
    createdAt: new Date(Date.now() - 20 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
    viewCount: 203,
  },
  {
    id: 'pattern-4',
    title: 'Data Quality Monitoring Dashboard',
    description: 'Real-time data quality monitoring dashboard with automated alerts and quality score calculations.',
    author: 'Emily Davis',
    authorEmail: 'emily.davis@example.com',
    team: 'Data Quality',
    tags: ['dashboard', 'monitoring', 'data-quality'],
    githubUrl: 'https://github.com/example/data-quality-dashboard',
    assetLinks: [
      { id: 'link-7', type: 'confluence', label: 'Quality Metrics', url: 'https://confluence.example.com/quality-metrics' },
      { id: 'link-8', type: 'video', label: 'Setup Guide', url: 'https://youtube.com/watch?v=demo3' },
    ],
    comments: [],
    createdAt: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    viewCount: 67,
  },
];

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const useAssetStore = create<AssetStore>((set) => ({
  designPatterns: mockDesignPatterns,

  fetchDesignPatterns: async () => {
    await delay(500);
    // In a real app, this would fetch from an API
    set({ designPatterns: mockDesignPatterns });
  },

  addDesignPattern: async (pattern) => {
    await delay(1000);
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

