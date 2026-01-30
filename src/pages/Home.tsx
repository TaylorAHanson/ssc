import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Sparkles, ArrowRight, Send, ExternalLink, ChevronDown, Shield, BarChart3, Activity,
  Database, Box, Server, CheckCircle, TrendingUp, AlertTriangle, FileText, Lock, Search, Info
} from 'lucide-react';

import { Textarea } from '../components/ui/textarea';
import { Button } from '../components/ui/button';
import type { ChatMessage, ConversationState } from '../types';
import { callAgent } from '../services/api';
import { useUserStore } from '../stores/userStore';
import type { UserPersona } from '../types';

type AgentMode = 'Self Service Agent' | 'Governance' | 'FinOps' | 'Data Quality';

const AGENT_SUGGESTIONS: Record<AgentMode, { label: string; query: string }[]> = {
  'Self Service Agent': [
    { label: 'Get workspace access', query: "I need to get access to a workspace for my team" },
    { label: 'Request data access', query: "I need access to some data tables for my project" },
    { label: 'Create a new workspace', query: "I'd like to create a new workspace for our analytics team" },
    { label: 'Provision service principal', query: "I need a service principal for my CI/CD pipeline" },
    { label: 'Create a new catalog', query: "I want to create a new catalog in Unity Catalog" },
    { label: 'Request GitHub repo', query: "I need a new GitHub repository for my Databricks project" },
    { label: 'Learn a new skill', query: "I want to learn new skills and improve my capabilities" },
    { label: 'Browse reusable assets', query: "I want to see what design patterns and templates are available" }
  ],
  'Governance': [
    { label: 'Overprovisioned users', query: "Which users are overprovisioned?" },
    { label: 'Recent changes', query: "What changed in the last 24 hours?" },
    { label: 'Workspace admins', query: "Who has workspace admin?" },
    { label: 'Account admins', query: "List all users with Account Admin role" },
    { label: 'Audit permissions', query: "Audit recent permission changes in the last 7 days" }
  ],
  'FinOps': [
    { label: 'Expensive workspaces', query: "Which workspaces are the most expensive?" },
    { label: 'Tagging compliance', query: "Which users are out of compliance with the tagging policy?" },
    { label: 'Cost trends', query: "Show monthly cost trend by department" },
    { label: 'Idle clusters', query: "Identify idle clusters that can be terminated" }
  ],
  'Data Quality': [
    { label: 'Quality drops', query: "Do we see any large drops in quality over the last 24 hours?" },
    { label: 'Schema drift', query: "List tables with schema drift in the last week" },
    { label: 'Freshness check', query: "Check freshness of gold-tier tables in the production catalog" }
  ]
};

interface DiscoveryItem {
  title: string;
  description: string;
  query: string;
}

interface DiscoveryColumn {
  title: string;
  icon: React.ReactNode;
  colorClass: string;
  hoverBorderClass: string;
  hoverTextClass: string;
  items: DiscoveryItem[];
}

const DISCOVERY_CONTENT: Record<AgentMode, DiscoveryColumn[]> = {
  'Self Service Agent': [
    {
      title: 'Enterprise Data',
      icon: <Database className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Discover Enterprise Data', description: 'Search and explore data assets', query: "I want to discover enterprise data" },
        { title: 'Request Data Access', description: 'Access via Catalog, Schema, or Table', query: "I need to request access to a dataset" },
        { title: 'Marketplace Certification', description: 'Certify assets for broader consumption', query: "I need to certify a dataset for the marketplace" },
      ]
    },
    {
      title: 'Platform Services',
      icon: <Box className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Workspace Access', description: 'Join an existing workspace', query: "I need access to a Databricks workspace" },
        { title: 'Provision Workspace', description: 'Create a new Databricks environment', query: "I need to provision a new Databricks workspace" },
        { title: 'Create Catalog or Schema', description: 'Create new data containers', query: "I need to create a new catalog or schema" },
        { title: 'Service Principal', description: 'For external apps, automation, and CI/CD pipelines', query: "I need a service principal for CI/CD" },
        { title: 'GitHub Repository', description: 'Create a new code repository', query: "I need to create a new GitHub repository" }
      ]
    },
    {
      title: 'Data Services',
      icon: <Server className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'REST API Access', description: 'Programmatic data access', query: "I need REST API access to data" },
        { title: 'Batch Data Access', description: 'High-volume data transfer', query: "I need batch data access via Delta Sharing" }
      ]
    }
  ],
  'Governance': [
    {
      title: 'Compliance & Security',
      icon: <Lock className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Policy Review', description: 'Request a formal policy review', query: "I need to request a policy review for my project" },
        { title: 'Security Baseline', description: 'Check workspace security standards', query: "Check if my workspace meets security baselines" }
      ]
    },
    {
      title: 'Data Stewardship',
      icon: <Shield className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Assign Owner', description: 'Update data asset ownership', query: "I need to assign a new owner to a catalog" },
        { title: 'Data Classification', description: 'Apply PII or sensitivity tags', query: "I need to classify sensitive data in my schema" }
      ]
    },
    {
      title: 'Audit & Tracking',
      icon: <FileText className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Access Report', description: 'See who can access your data', query: "Show me an access report for my production data" },
        { title: 'Usage Audit', description: 'Review recent administrative actions', query: "Audit administrative actions in my workspace" }
      ]
    }
  ],
  'FinOps': [
    {
      title: 'Cost Analysis',
      icon: <BarChart3 className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Usage Forecast', description: 'Predict future spending', query: "What is my predicted spend for next month?" },
        { title: 'Department Billing', description: 'Breakdown by cost center', query: "Show me the cost breakdown by department" }
      ]
    },
    {
      title: 'Budgeting',
      icon: <TrendingUp className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Set Budget Alert', description: 'Get notified of overages', query: "I want to set a cost alert for my workspace" },
        { title: 'Budget Review', description: 'Compare actual vs. planned', query: "Review my team's budget performance" }
      ]
    },
    {
      title: 'Optimization',
      icon: <CheckCircle className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Idle Clusters', description: 'Terminate unused compute', query: "Identify idle clusters I can safely terminate" },
        { title: 'Spot Instances', description: 'Analyze spot adoption', query: "Show me my spot instance savings report" }
      ]
    }
  ],
  'Data Quality': [
    {
      title: 'Health Checks',
      icon: <Activity className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Schema Validation', description: 'Detect structural drift', query: "Check for schema drift in my bronze tables" },
        { title: 'Null Check Report', description: 'Monitor field completeness', query: "Show me the null-value report for my core tables" }
      ]
    },
    {
      title: 'Monitoring',
      icon: <Search className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Pipeline SLA', description: 'Track data arrival times', query: "Are my production pipelines meeting SLAs?" },
        { title: 'Data Freshness', description: 'Verify update frequency', query: "Check freshness of my dashboard source tables" }
      ]
    },
    {
      title: 'Validation',
      icon: <AlertTriangle className="w-5 h-5" />,
      colorClass: 'text-primary',
      hoverBorderClass: 'hover:border-primary/50',
      hoverTextClass: 'group-hover:text-primary',
      items: [
        { title: 'Range Checks', description: 'Verify value constraints', query: "Run value range validation on my transaction data" },
        { title: 'Cross-Source Sync', description: 'Compare with source systems', query: "Validate my lakehouse data against the source DB" }
      ]
    }
  ]
};

const MODE_ICONS: Record<AgentMode, React.ReactNode> = {
  'Self Service Agent': <Sparkles className="w-3.5 h-3.5" />,
  'Governance': <Shield className="w-3.5 h-3.5" />,
  'FinOps': <BarChart3 className="w-3.5 h-3.5" />,
  'Data Quality': <Activity className="w-3.5 h-3.5" />
};

const MODE_PERMISSIONS: Record<AgentMode, UserPersona[]> = {
  'Self Service Agent': ['Platform Admin', 'Business User', 'Governance Admin', 'Finance Admin', 'Security Admin'],
  'Governance': ['Platform Admin', 'Governance Admin', 'Security Admin'],
  'FinOps': ['Platform Admin', 'Finance Admin'],
  'Data Quality': ['Platform Admin', 'Governance Admin']
};

// Determine which form route to use based on conversation (fallback only for error cases)
const determineFormRoute = (query: string, _answers: Record<string, string | string[]>, context?: { type: 'paas' | 'daas'; title: string }): { path: string; title: string } => {
  const lowerQuery = query.toLowerCase();

  if (lowerQuery.includes('workspace access') || (lowerQuery.includes('workspace') && lowerQuery.includes('access'))) {
    return { path: '/paas/workspace-access', title: 'Get Workspace Access' };
  } else if (lowerQuery.includes('catalog') || lowerQuery.includes('schema') || lowerQuery.includes('table')) {
    if (lowerQuery.includes('create') || lowerQuery.includes('new')) {
      return { path: '/paas/request-catalog', title: 'Create Catalog/Schema/Table' };
    } else {
      return { path: '/paas/request-access', title: 'Request Data Access' };
    }
  } else if (lowerQuery.includes('data access')) {
    return { path: '/paas/request-access', title: 'Request Data Access' };
  } else if (lowerQuery.includes('workspace') && (lowerQuery.includes('provision') || lowerQuery.includes('new'))) {
    return { path: '/paas/provision-workspace', title: 'Provision New Workspace' };
  } else if (lowerQuery.includes('service principal')) {
    return { path: '/paas/service-principal', title: 'Provision Service Principal' };
  } else if (lowerQuery.includes('marketplace') || lowerQuery.includes('certification')) {
    return { path: '/paas/marketplace', title: 'Marketplace Certification' };
  } else if (lowerQuery.includes('github') || lowerQuery.includes('repo') || lowerQuery.includes('repository') || lowerQuery.includes('git')) {
    return { path: '/paas/github-repo-creation', title: 'GitHub Repository Creation' };
  } else if (lowerQuery.includes('rest api') || (lowerQuery.includes('api') && !lowerQuery.includes('batch'))) {
    return { path: '/daas/rest-api', title: 'Request REST API Access' };
  } else if (lowerQuery.includes('batch') || lowerQuery.includes('delta sharing')) {
    return { path: '/daas/batch-data', title: 'Request Batch Data Access' };
  } else if (context?.type === 'paas') {
    // Default PAAS routes based on context
    if (context.title.includes('Workspace Access')) {
      return { path: '/paas/workspace-access', title: 'Get Workspace Access' };
    } else if (context.title.includes('Catalog')) {
      return { path: '/paas/request-catalog', title: 'Create Catalog/Schema/Table' };
    } else if (context.title.includes('Data Access')) {
      return { path: '/paas/request-access', title: 'Request Data Access' };
    } else if (context.title.includes('Provision') && context.title.includes('Workspace')) {
      return { path: '/paas/provision-workspace', title: 'Provision New Workspace' };
    } else if (context.title.includes('Service Principal')) {
      return { path: '/paas/service-principal', title: 'Provision Service Principal' };
    } else if (context.title.includes('Marketplace')) {
      return { path: '/paas/marketplace', title: 'Marketplace Certification' };
    } else if (context.title.includes('GitHub')) {
      return { path: '/paas/github-repo-creation', title: 'GitHub Repository Creation' };
    }
  } else if (context?.type === 'daas') {
    if (context.title.includes('REST API')) {
      return { path: '/daas/rest-api', title: 'Request REST API Access' };
    } else if (context.title.includes('Batch')) {
      return { path: '/daas/batch-data', title: 'Request Batch Data Access' };
    }
  }

  // Default fallback
  return { path: '/paas/request-access', title: 'Request Data Access' };
};

const ThinkingBubble = () => (
  <div className="flex items-center gap-1 px-1 py-1">
    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"></div>
  </div>
);

import { useBrandingStore } from '../stores/brandingStore';

export function Home() {
  const [query, setQuery] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [conversationState, setConversationState] = useState<ConversationState | null>(null);
  const [agentMode, setAgentMode] = useState<AgentMode>(() => {
    // Initialize from localStorage directly to avoid race conditions
    const savedMode = localStorage.getItem('atlas_agent_mode');
    return (savedMode as AgentMode) || 'Self Service Agent';
  });

  const [showModeDropdown, setShowModeDropdown] = useState(false);
  const currentPersona = useUserStore((state) => state.currentPersona);
  const navigate = useNavigate();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { brandName, brandLogoUrl } = useBrandingStore();
  const isInitialized = useUserStore((state) => state.isInitialized);

  const adjustHeight = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  };

  const handleReset = () => {
    setConversationState(null);
    setQuery('');
    // Ensure we KEEP the current mode
    // setAgentMode('Self Service Agent');
    setIsProcessing(false);
    localStorage.removeItem('atlas_conversation_state');
    // localStorage.removeItem('atlas_agent_mode');

    setTimeout(() => {
      const initialTextarea = document.querySelector('textarea');
      if (initialTextarea instanceof HTMLTextAreaElement) {
        initialTextarea.focus();
      }
    }, 100);
  };

  // Load state from localStorage on mount (Conversation Only)
  useEffect(() => {
    const savedState = localStorage.getItem('atlas_conversation_state');
    if (savedState) {
      try {
        const parsedState = JSON.parse(savedState);
        if (parsedState.messages) {
          parsedState.messages = parsedState.messages.map((m: any) => ({
            ...m,
            timestamp: new Date(m.timestamp)
          }));
        }
        setConversationState(parsedState);
      } catch (e) {
        console.error('Failed to parse saved conversation state', e);
      }
    }
  }, []); // Only run once on mount

  // Persist state to localStorage
  useEffect(() => {
    if (conversationState) {
      localStorage.setItem('atlas_conversation_state', JSON.stringify(conversationState));
    }
    localStorage.setItem('atlas_agent_mode', agentMode);
  }, [conversationState, agentMode]);

  // Reset agent mode only if current persona explicitly FORBIDS it
  useEffect(() => {
    if (isInitialized && currentPersona && MODE_PERMISSIONS[agentMode]) {
      if (!MODE_PERMISSIONS[agentMode].includes(currentPersona)) {
        // If the user's persona doesn't allow this mode, reset to Self Service
        console.warn(`Resetting mode from ${agentMode} because persona ${currentPersona} does not allow it.`);
        setAgentMode('Self Service Agent');
      }
    }
  }, [currentPersona, agentMode, isInitialized]);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowModeDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversationState?.messages, conversationState?.currentQuestionIndex]);

  // Focus input when conversation starts
  useEffect(() => {
    if (conversationState && !conversationState.showConfirmation) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [conversationState?.currentQuestionIndex, conversationState?.showConfirmation]);


  const handleInitialSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isProcessing) return;

    const initialQuery = query.trim();
    setQuery(''); // Clear input immediately
    setIsProcessing(true);

    // Create initial user message
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      type: 'user',
      content: initialQuery,
      timestamp: new Date(),
    };

    // Create thinking message
    const thinkingMessage: ChatMessage = {
      id: "thinking",
      type: 'agent',
      content: '__THINKING__',
      timestamp: new Date(),
    };

    // Optimistically set state with thinking bubble
    setConversationState({
      initialQuery,
      messages: [userMessage, thinkingMessage],
      currentQuestionIndex: 0,
      followUpQuestions: [],
      answers: {},
      agentActions: [],
      showConfirmation: false,
      context: undefined,
    });

    try {
      // Call the real agent endpoint
      const agentResponse = await callAgent({
        query: initialQuery,
        conversation_history: [], // First message, no history
        context: {
          agent_mode: agentMode
        },
      });

      // Use only agent-provided questions (no fallback)
      const followUpQuestions = agentResponse.follow_up_questions || [];

      // Create agent message from response
      let agentMessageContent = agentResponse.message;
      if (!agentMessageContent || !agentMessageContent.trim()) {
        agentMessageContent = "I'm processing your request.";
      }

      const agentMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: agentMessageContent,
        timestamp: new Date(),
      };

      // Create messages array (replace thinking with real response)
      const messages: ChatMessage[] = [userMessage, agentMessage];

      // Add first question if agent provided one
      if (followUpQuestions.length > 0) {
        messages.push({
          id: (Date.now() + 2).toString(),
          type: 'agent',
          content: followUpQuestions[0].question,
          timestamp: new Date(),
        });
      }

      // Determine if we should show confirmation
      const showConfirmation = !agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined);

      setConversationState(prev => prev ? {
        ...prev,
        messages,
        followUpQuestions,
        showConfirmation,
        formRoute: agentResponse.form_route || undefined,
        formPrefillData: agentResponse.form_prefill_data,
        context: prev.context // Preserve context if set (unlikely for initial)
      } : null);
    } catch (error) {
      console.error('Error calling agent:', error);
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: error instanceof Error
          ? `I'm having trouble connecting to the agent service. ${error.message}. Please try again.`
          : "I'm having trouble connecting to the agent service. Please try again.",
        timestamp: new Date(),
      };

      setConversationState(prev => prev ? {
        ...prev,
        messages: [userMessage, errorMessage],
        showConfirmation: false
      } : null);
    }

    setIsProcessing(false);
  };

  const handleAnswerSubmit = async (questionId: string, answer: string | string[]) => {
    if (!conversationState || isProcessing) return;

    setIsProcessing(true);

    const updatedAnswers = {
      ...conversationState.answers,
      [questionId]: answer,
    };

    const answerMessage: ChatMessage = {
      id: Date.now().toString(),
      type: 'user',
      content: Array.isArray(answer) ? answer.join(', ') : answer,
      timestamp: new Date(),
    };

    // Create thinking message
    const thinkingMessage: ChatMessage = {
      id: "thinking",
      type: 'agent',
      content: '__THINKING__',
      timestamp: new Date(),
    };

    // Optimistically update
    setConversationState({
      ...conversationState,
      messages: [...conversationState.messages, answerMessage, thinkingMessage],
      answers: updatedAnswers,
    });

    try {
      // Call the agent with the answer to get next question or form route
      const agentResponse = await callAgent({
        query: `Answer to "${conversationState.followUpQuestions[conversationState.currentQuestionIndex]?.question}": ${Array.isArray(answer) ? answer.join(', ') : answer}`,
        conversation_history: conversationState.messages.map(m => ({
          id: m.id,
          type: m.type,
          content: m.content,
          timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : (typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString()),
        })),
        context: {
          ...conversationState.context,
          agent_mode: agentMode
        },
      });

      // Use only agent-provided questions (no fallback)
      const followUpQuestions = agentResponse.follow_up_questions || [];
      const nextIndex = agentResponse.follow_up_questions ? 0 : conversationState.currentQuestionIndex + 1;
      const hasMoreQuestions = agentResponse.requires_more_info && followUpQuestions.length > 0;

      // Create agent message from response
      let agentMessageContent = agentResponse.message;
      if (!agentMessageContent || !agentMessageContent.trim()) {
        agentMessageContent = "Thank you for that information.";
      }

      const agentMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: agentMessageContent,
        timestamp: new Date(),
      };

      const messages: ChatMessage[] = [...conversationState.messages, answerMessage, agentMessage];

      // Add next question if agent provided one
      if (hasMoreQuestions && followUpQuestions.length > 0) {
        const nextQuestion = followUpQuestions[0];
        messages.push({
          id: (Date.now() + 2).toString(),
          type: 'agent',
          content: nextQuestion.question,
          timestamp: new Date(),
        });
      }

      // Determine if we should show confirmation (form route ready)
      const showConfirmation = conversationState?.showConfirmation || (!agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined));

      setConversationState(prev => prev ? {
        ...prev,
        messages,
        currentQuestionIndex: agentResponse.follow_up_questions ? 0 : nextIndex, // Reset if new questions
        followUpQuestions,
        answers: updatedAnswers, // Keep updated answers
        showConfirmation,
        formRoute: agentResponse.form_route || prev.formRoute,
        formPrefillData: agentResponse.form_prefill_data || prev.formPrefillData,
      } : null);
    } catch (error) {
      console.error('Error calling agent:', error);
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        type: 'agent',
        content: error instanceof Error
          ? `I'm having trouble processing your answer. ${error.message}. Please try again.`
          : "I'm having trouble processing your answer. Please try again.",
        timestamp: new Date(),
      };

      setConversationState(prev => prev ? {
        ...prev,
        messages: [...conversationState.messages, answerMessage, errorMessage],
        answers: updatedAnswers,
      } : null);
    }

    setIsProcessing(false);
  };

  const getButtonLabel = (path: string | undefined): string => {
    if (!path) return 'Continue to form';

    // Determine button label based on route
    if (path.startsWith('/paas/') || path.startsWith('/daas/')) {
      return 'Go to pre-filled form';
    } else if (path.includes('/community/links') || path === '/community-links') {
      return 'Go to community links';
    } else if (path.includes('/community/assets') || path === '/reusable-assets') {
      return 'View reusable assets';
    } else if (path.includes('/community/training') || path === '/training') {
      return 'Go to training';
    } else if (path.includes('/community/events') || path === '/events') {
      return 'View events';
    } else {
      // Fallback: try to extract meaningful label from path
      const pathParts = path.split('/').filter(Boolean);
      if (pathParts.length > 0) {
        const lastPart = pathParts[pathParts.length - 1];
        return `Go to ${lastPart.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}`;
      }
      return 'Continue';
    }
  };

  const handleGoToForm = () => {
    if (!conversationState?.formRoute?.path) {
      console.error('No form route path available');
      return;
    }

    const routePath = conversationState.formRoute.path;

    // Store prefilled data in localStorage before navigating
    // Use form_prefill_data from agent if available, otherwise use answers
    const prefillData = conversationState.formPrefillData || conversationState.answers;
    if (prefillData && Object.keys(prefillData).length > 0) {
      localStorage.setItem(`form_prefill_${routePath}`, JSON.stringify(prefillData));
    }

    // Navigate in the same window
    navigate(routePath);
  };


  // If we have a conversation in progress, show chat interface
  if (conversationState) {
    const currentQuestion = conversationState.followUpQuestions[conversationState.currentQuestionIndex];
    const allQuestionsAnswered = conversationState.currentQuestionIndex >= conversationState.followUpQuestions.length;

    return (
      <div className="flex flex-col h-[calc(100vh-200px)] relative">
        {/* Background gradient */}
        <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-primary/10 pointer-events-none" />

        <div className="text-center mb-6 relative z-10">
          <div className="flex items-center justify-center gap-3 mb-3">
            {brandLogoUrl ? (
              <img src={brandLogoUrl} alt="Logo" className="w-10 h-10 object-contain rounded-xl" />
            ) : (
              <div className="p-2 bg-primary rounded-xl shadow-md">
                <Sparkles className="w-5 h-5 text-white" />
              </div>
            )}
            <h1 className="text-3xl font-bold text-gray-900">
              {conversationState.context?.title || brandName}
            </h1>
          </div>
        </div>

        <div className="relative flex-1 flex flex-col">
          {/* Glow effect */}
          <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 to-primary/10 rounded-3xl blur-xl opacity-30" />

          <div className="relative flex-1 flex flex-col bg-white/80 backdrop-blur-sm rounded-3xl shadow-2xl border border-gray-200/50 overflow-hidden">
            <div className="flex-1 flex flex-col p-6 overflow-hidden">
              {/* Messages */}
              <div className="flex-1 overflow-y-auto space-y-4 mb-4 pr-2 custom-scrollbar">
                {conversationState.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`flex ${message.type === 'user' ? 'justify-end' : 'justify-start'} animate-in fade-in slide-in-from-bottom-2 duration-300`}
                  >
                    <div
                      className={`max-w-[80%] rounded-2xl px-4 py-3 shadow-sm ${message.type === 'user'
                        ? 'bg-primary text-white'
                        : 'bg-gray-50 text-gray-900 border border-gray-200/50'
                        }`}
                    >
                      {message.content === '__THINKING__' ? (
                        <ThinkingBubble />
                      ) : message.type === 'agent' ? (
                        <div
                          className="text-sm leading-relaxed prose prose-sm max-w-none [&_a]:text-blue-600 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-blue-700 [&_a:visited]:text-purple-600"
                          dangerouslySetInnerHTML={{ __html: message.content }}
                        />
                      ) : (
                        <p className="text-sm leading-relaxed">{message.content}</p>
                      )}
                    </div>
                  </div>
                ))}

                {/* Form Link */}
                {conversationState.showConfirmation && conversationState.formRoute && (
                  <div className="space-y-4 mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="bg-gradient-to-br from-blue-50 to-primary/5 border border-blue-200/50 rounded-2xl p-5 shadow-sm">
                      <div className="flex items-center justify-between">
                        <div className="flex-1">
                          <Button
                            onClick={handleGoToForm}
                            className="flex items-center gap-2 rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                          >
                            <ExternalLink className="w-4 h-4" />
                            {getButtonLabel(conversationState.formRoute?.path)}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>

              {/* Input Area */}
              {/* Show input for specific question types */}
              {!allQuestionsAnswered && currentQuestion && (
                <div className="border-t border-gray-200/50 pt-5 mt-4">
                  <div className="space-y-3">
                    {currentQuestion.type === 'text' && (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          const input = e.currentTarget.querySelector('input') as HTMLInputElement;
                          if (input.value.trim()) {
                            handleAnswerSubmit(currentQuestion.id, input.value.trim());
                            input.value = '';
                          }
                        }}
                      >
                        <div className="flex gap-2 items-center">
                          <Textarea
                            placeholder="Type your answer..."
                            required={currentQuestion.required}
                            className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[48px] max-h-[200px] resize-none overflow-hidden py-3"
                            disabled={isProcessing}
                            rows={1}
                            onInput={(e) => adjustHeight(e.currentTarget)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                const form = e.currentTarget.closest('form');
                                if (form && !isProcessing) {
                                  form.requestSubmit();
                                }
                              }
                            }}
                          />
                          <Button
                            type="submit"
                            disabled={isProcessing}
                            className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 h-10"
                          >
                            <Send className="w-4 h-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={handleReset}
                            className="rounded-xl border-primary/30 text-primary hover:bg-primary/5 hover:text-primary/80 transition-all duration-200 h-10 px-4 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap"
                          >
                            New Chat
                          </Button>
                        </div>

                        <div className="mt-2 flex items-center gap-2 px-1 relative" ref={dropdownRef}>
                          <button
                            type="button"
                            onClick={() => setShowModeDropdown(!showModeDropdown)}
                            className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900 transition-colors duration-200 py-1"
                          >
                            <span className="flex items-center gap-1.5">
                              {MODE_ICONS[agentMode]}
                              {agentMode}
                            </span>
                            <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${showModeDropdown ? 'rotate-180' : ''}`} />
                          </button>

                          {showModeDropdown && (
                            <div className="absolute bottom-full left-0 mb-1 w-48 bg-white rounded-xl shadow-xl border border-gray-200 py-1.5 z-50 animate-in fade-in slide-in-from-bottom-2 duration-200">
                              {(Object.keys(AGENT_SUGGESTIONS) as AgentMode[])
                                .filter(mode => MODE_PERMISSIONS[mode].includes(currentPersona))
                                .map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    onClick={() => {
                                      setAgentMode(mode);
                                      setShowModeDropdown(false);
                                    }}
                                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-xs transition-colors duration-200 ${agentMode === mode
                                      ? 'bg-primary/5 text-primary font-semibold'
                                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                      }`}
                                  >
                                    <span className={`${agentMode === mode ? 'text-primary' : 'text-gray-400'}`}>
                                      {MODE_ICONS[mode]}
                                    </span>
                                    {mode}
                                  </button>
                                ))}
                            </div>
                          )}
                        </div>
                      </form>
                    )}

                    {currentQuestion.type === 'radio' && (
                      <div className="space-y-2">
                        {currentQuestion.options?.map((option) => (
                          <button
                            key={option}
                            onClick={() => handleAnswerSubmit(currentQuestion.id, option)}
                            className="w-full text-left px-4 py-3 bg-gray-50 hover:bg-gradient-to-r hover:from-primary/10 hover:to-primary/5 rounded-xl border border-gray-200/50 hover:border-primary/30 transition-all duration-200 hover:shadow-sm text-sm"
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    )}

                    {currentQuestion.type === 'multi-select' && (
                      <div className="space-y-2">
                        {currentQuestion.options?.map((option) => {
                          const isSelected = Array.isArray(conversationState.answers[currentQuestion.id]) &&
                            conversationState.answers[currentQuestion.id].includes(option);
                          return (
                            <button
                              key={option}
                              onClick={() => {
                                const current = Array.isArray(conversationState.answers[currentQuestion.id])
                                  ? conversationState.answers[currentQuestion.id] as string[]
                                  : [];
                                const updated = isSelected
                                  ? current.filter(o => o !== option)
                                  : [...current, option];
                                handleAnswerSubmit(currentQuestion.id, updated);
                              }}
                              className={`w-full text-left px-4 py-3 rounded-xl border transition-all duration-200 text-sm ${isSelected
                                ? 'bg-gradient-to-r from-primary to-primary/90 text-white border-primary shadow-md'
                                : 'bg-gray-50 hover:bg-gradient-to-r hover:from-primary/10 hover:to-primary/5 border-gray-200/50 hover:border-primary/30 hover:shadow-sm'
                                }`}
                            >
                              {option}
                            </button>
                          );
                        })}
                        {Array.isArray(conversationState.answers[currentQuestion.id]) &&
                          conversationState.answers[currentQuestion.id].length > 0 && (
                            <Button
                              onClick={() => {
                                const answer = conversationState.answers[currentQuestion.id];
                                if (Array.isArray(answer) && answer.length > 0) {
                                  // Move to next question
                                  const nextIndex = conversationState.currentQuestionIndex + 1;
                                  if (nextIndex < conversationState.followUpQuestions.length) {
                                    const nextQuestion = conversationState.followUpQuestions[nextIndex];
                                    const agentMessage: ChatMessage = {
                                      id: Date.now().toString(),
                                      type: 'agent',
                                      content: nextQuestion.question,
                                      timestamp: new Date(),
                                    };
                                    setConversationState({
                                      ...conversationState,
                                      messages: [...conversationState.messages, agentMessage],
                                      currentQuestionIndex: nextIndex,
                                    });
                                  } else {
                                    // All questions answered, determine form route
                                    const formRoute = determineFormRoute(
                                      conversationState.initialQuery,
                                      conversationState.answers,
                                      conversationState.context
                                    );

                                    // Store prefilled data in localStorage for the form page
                                    localStorage.setItem(`form_prefill_${formRoute.path}`, JSON.stringify(conversationState.answers));

                                    const agentMessage: ChatMessage = {
                                      id: Date.now().toString(),
                                      type: 'agent',
                                      content: `I have prefilled the correct form based on your input. Click the link below to review and submit.`,
                                      timestamp: new Date(),
                                    };
                                    setConversationState({
                                      ...conversationState,
                                      messages: [...conversationState.messages, agentMessage],
                                      currentQuestionIndex: nextIndex,
                                      agentActions: [],
                                      showConfirmation: true,
                                      formRoute,
                                    });
                                  }
                                }
                              }}
                              className="w-full mt-3 rounded-xl shadow-md hover:shadow-lg transition-all duration-200"
                            >
                              Continue
                            </Button>
                          )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* General text input - always available when conversation is active and no specific question */}
              {(!currentQuestion || allQuestionsAnswered) && (
                <div className="border-t border-gray-200/50 pt-5 mt-4">
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      const textarea = e.currentTarget.querySelector('textarea') as HTMLTextAreaElement;
                      const userMessage = textarea.value.trim();
                      if (userMessage && !isProcessing) {
                        setIsProcessing(true);
                        textarea.value = ''; // Clear input immediately

                        const userMsg: ChatMessage = {
                          id: Date.now().toString(),
                          type: 'user',
                          content: userMessage,
                          timestamp: new Date(),
                        };

                        const thinkingMsg: ChatMessage = {
                          id: "thinking",
                          type: 'agent',
                          content: '__THINKING__',
                          timestamp: new Date(),
                        };

                        // Optimistic update
                        setConversationState({
                          ...conversationState,
                          messages: [...conversationState.messages, userMsg, thinkingMsg],
                        });

                        try {
                          // Call agent with the free-form message
                          const agentResponse = await callAgent({
                            query: userMessage,
                            conversation_history: conversationState.messages.map(m => ({
                              id: m.id,
                              type: m.type,
                              content: m.content,
                              timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : (typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString()),
                            })),
                            context: {
                              ...conversationState.context,
                              agent_mode: agentMode
                            },
                          });

                          const agentMsg: ChatMessage = {
                            id: (Date.now() + 1).toString(),
                            type: 'agent',
                            content: agentResponse.message || "I understand. Let me help you with that.",
                            timestamp: new Date(),
                          };

                          const followUpQuestions = agentResponse.follow_up_questions || [];
                          const messages: ChatMessage[] = [...conversationState.messages, userMsg, agentMsg];

                          // Add first question if agent provided one
                          if (followUpQuestions.length > 0) {
                            messages.push({
                              id: (Date.now() + 2).toString(),
                              type: 'agent',
                              content: followUpQuestions[0].question,
                              timestamp: new Date(),
                            });
                          }

                          // Determine if we should show confirmation
                          const showConfirmation = conversationState.showConfirmation || (!agentResponse.requires_more_info && (agentResponse.form_route !== null || agentResponse.form_prefill_data !== undefined));

                          setConversationState(prev => prev ? {
                            ...prev,
                            messages,
                            currentQuestionIndex: followUpQuestions.length > 0 ? 0 : prev.currentQuestionIndex,
                            followUpQuestions,
                            showConfirmation,
                            formRoute: agentResponse.form_route || prev.formRoute,
                            formPrefillData: agentResponse.form_prefill_data || prev.formPrefillData,
                          } : null);

                          // Store prefilled data if available
                          if (agentResponse.form_route) {
                            const prefillData = agentResponse.form_prefill_data || conversationState.answers;
                            if (prefillData && Object.keys(prefillData).length > 0) {
                              localStorage.setItem(`form_prefill_${agentResponse.form_route.path}`, JSON.stringify(prefillData));
                            }
                          }
                        } catch (error) {
                          console.error('Error calling agent:', error);
                          const errorMsg: ChatMessage = {
                            id: (Date.now() + 1).toString(),
                            type: 'agent',
                            content: error instanceof Error
                              ? `I'm having trouble processing your message. ${error.message}. Please try again.`
                              : "I'm having trouble processing your message. Please try again.",
                            timestamp: new Date(),
                          };
                          setConversationState(prev => prev ? {
                            ...prev,
                            messages: [...conversationState.messages, userMsg, errorMsg],
                          } : null);
                        } finally {
                          setIsProcessing(false);
                        }
                      }
                    }}
                  >
                    <div className="flex gap-2 items-center">
                      <Textarea
                        ref={inputRef}
                        placeholder="Type your message..."
                        className="flex-1 rounded-xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 min-h-[48px] max-h-[200px] resize-none overflow-hidden py-3"
                        disabled={false} // Allow typing during processing
                        onInput={(e) => adjustHeight(e.currentTarget)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            const form = e.currentTarget.closest('form');
                            if (form && !isProcessing && e.currentTarget.value.trim()) {
                              form.requestSubmit();
                            }
                          }
                        }}
                      />
                      <Button
                        type="submit"
                        disabled={isProcessing}
                        className="rounded-xl shadow-md hover:shadow-lg transition-all duration-200 self-end h-10 disabled:opacity-50"
                      >
                        {isProcessing ? (
                          <div className="w-4 h-4 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
                        ) : (
                          <Send className="w-4 h-4" />
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={handleReset}
                        className="rounded-xl border-primary/30 text-primary hover:bg-primary/5 hover:text-primary/80 transition-all duration-200 self-end h-10 px-4 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap"
                      >
                        New Chat
                      </Button>
                    </div>

                    <div className="mt-2 flex items-center gap-2 px-1 relative" ref={dropdownRef}>
                      <button
                        type="button"
                        onClick={() => setShowModeDropdown(!showModeDropdown)}
                        className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900 transition-colors duration-200 py-1"
                      >
                        <span className="flex items-center gap-1.5">
                          {MODE_ICONS[agentMode]}
                          {agentMode}
                        </span>
                        <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${showModeDropdown ? 'rotate-180' : ''}`} />
                      </button>

                      {showModeDropdown && (
                        <div className="absolute bottom-full left-0 mb-1 w-48 bg-white rounded-xl shadow-xl border border-gray-200 py-1.5 z-50 animate-in fade-in slide-in-from-bottom-2 duration-200">
                          {(Object.keys(AGENT_SUGGESTIONS) as AgentMode[])
                            .filter(mode => MODE_PERMISSIONS[mode].includes(currentPersona))
                            .map((mode) => (
                              <button
                                key={mode}
                                type="button"
                                onClick={() => {
                                  setAgentMode(mode);
                                  setShowModeDropdown(false);
                                }}
                                className={`w-full flex items-center gap-2.5 px-3 py-2 text-xs transition-colors duration-200 ${agentMode === mode
                                  ? 'bg-primary/5 text-primary font-semibold'
                                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                  }`}
                              >
                                <span className={`${agentMode === mode ? 'text-primary' : 'text-gray-400'}`}>
                                  {MODE_ICONS[mode]}
                                </span>
                                {mode}
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                  </form>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Initial state - show input form
  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-200px)] relative">
      <div className="w-full max-w-3xl">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-3 mb-6">
            {brandLogoUrl ? (
              <img src={brandLogoUrl} alt="Logo" className="w-16 h-16 object-contain rounded-2xl" />
            ) : (
              <div className="p-3 bg-gradient-to-br from-primary to-primary/80 rounded-2xl shadow-lg">
                <Sparkles className="w-8 h-8 text-white" />
              </div>
            )}
            <h1 className="text-5xl font-bold bg-gradient-to-r from-gray-900 to-gray-700 bg-clip-text text-transparent">
              {brandName}
            </h1>
          </div>
          <p className="text-lg text-gray-600 max-w-xl mx-auto">
            Your intelligent assistant for {brandName} self-service requests.
          </p>
        </div>

        <div className="relative">
          {/* Glow effect */}
          <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 to-primary/10 rounded-3xl blur-xl opacity-30" />

          <div className="relative bg-white/90 backdrop-blur-sm rounded-3xl shadow-xl border border-gray-200/50 overflow-hidden">
            <div className="p-8 md:p-10">
              <form onSubmit={handleInitialSubmit} className="space-y-4">
                <div className="relative group">
                  <div className="absolute inset-0 bg-gradient-to-r from-primary/10 to-transparent rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                  <Textarea
                    ref={inputRef}
                    placeholder="What do you need to do today?"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      adjustHeight(e.target);
                    }}
                    className="text-base py-3 pr-14 rounded-2xl border-2 border-gray-200 focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all duration-200 bg-white/90 backdrop-blur-sm min-h-[52px] max-h-[200px] resize-none overflow-hidden custom-scrollbar"
                    disabled={false}
                    rows={1}
                    onInput={(e) => adjustHeight(e.currentTarget)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        if (!isProcessing && query.trim()) {
                          handleInitialSubmit(e);
                        }
                      }
                    }}
                  />
                  <Button
                    type="submit"
                    disabled={isProcessing || !query.trim()}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-xl shadow-lg hover:shadow-xl transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    size="sm"
                  >
                    {isProcessing ? (
                      <span className="animate-pulse px-3">Processing...</span>
                    ) : (
                      <ArrowRight className="w-4 h-4" />
                    )}
                  </Button>
                </div>

                <div className="flex items-center justify-between px-1 relative">
                  <div ref={dropdownRef}>
                    <button
                      type="button"
                      onClick={() => setShowModeDropdown(!showModeDropdown)}
                      className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900 transition-colors duration-200 py-1"
                    >
                      <span className="flex items-center gap-1.5">
                        {MODE_ICONS[agentMode]}
                        {agentMode}
                      </span>
                      <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${showModeDropdown ? 'rotate-180' : ''}`} />
                    </button>

                    {showModeDropdown && (
                      <div className="absolute top-full left-0 mt-1 w-48 bg-white rounded-xl shadow-xl border border-gray-200 py-1.5 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
                        {(Object.keys(AGENT_SUGGESTIONS) as AgentMode[])
                          .filter(mode => MODE_PERMISSIONS[mode].includes(currentPersona))
                          .map((mode) => (
                            <button
                              key={mode}
                              type="button"
                              onClick={() => {
                                setAgentMode(mode);
                                setShowModeDropdown(false);
                              }}
                              className={`w-full flex items-center gap-2.5 px-3 py-2 text-xs transition-colors duration-200 ${agentMode === mode
                                ? 'bg-primary/5 text-primary font-semibold'
                                : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                }`}
                            >
                              <span className={`${agentMode === mode ? 'text-primary' : 'text-gray-400'}`}>
                                {MODE_ICONS[mode]}
                              </span>
                              {mode}
                            </button>
                          ))}
                      </div>
                    )}
                  </div>
                </div>
              </form>

              {/* Quick suggestions */}
              <div className="mt-6 pt-6 border-t border-gray-200/50">
                {/* Service Discovery Categories */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-left">
                  {DISCOVERY_CONTENT[agentMode].map((column, idx) => (
                    <div key={idx} className="space-y-4">
                      <div className={`flex items-center gap-2 font-semibold ${column.colorClass}`}>
                        {column.icon}
                        <h3>{column.title}</h3>
                      </div>
                      <div className="grid gap-2">
                        {column.items.map((item, itemIdx) => (
                          <button
                            key={itemIdx}
                            onClick={() => {
                              setQuery(item.query);
                              inputRef.current?.focus();
                            }}
                            className={`relative p-2.5 rounded-xl border border-gray-200 hover:shadow-md hover:bg-white/80 transition-all duration-200 text-left group ${column.hoverBorderClass}`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className={`text-sm font-medium text-gray-900 transition-colors ${column.hoverTextClass}`}>{item.title}</div>
                              <div className="relative group/info">
                                <Info className="w-4 h-4 text-gray-400 hover:text-gray-600 transition-colors" />
                                {/* Tooltip */}
                                <div className="absolute bottom-full right-0 mb-2 w-48 p-2 bg-gray-900 text-white text-xs rounded-lg shadow-xl opacity-0 translate-y-2 invisible group-hover/info:opacity-100 group-hover/info:translate-y-0 group-hover/info:visible transition-all duration-200 z-50 pointer-events-none">
                                  {item.description}
                                  {/* Arrow */}
                                  <div className="absolute top-full right-1.5 -mt-1 border-4 border-transparent border-t-gray-900" />
                                </div>
                              </div>
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div >
  );
}
