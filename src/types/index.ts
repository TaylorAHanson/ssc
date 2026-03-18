export type RequestStatus =
  | 'pending'
  | 'manager_approval'
  | 'training_pending'
  | 'provisioning'
  | 'completed'
  | 'rejected'
  | 'failed';

export type RequestType =
  | 'workspace_access'
  | 'catalog_schema_table'
  | 'catalog_schema_table_access'
  | 'workspace_provision'
  | 'service_principal'
  | 'marketplace_certification'
  | 'rest_api_access'
  | 'batch_data_access'
  | 'github_repo_creation';

export interface Role {
  id: string;
  name: string; // e.g., 'platform_admin'
  description?: string;
}

export interface User {
  id: string;
  email: string;
  full_name?: string;
  is_active: boolean;
  roles: Role[];
  created_at: string;
  updated_at: string;
}

export type UserPersona = 'Platform Admin' | 'Governance Admin' | 'Security Admin' | 'Finance Admin' | 'Business User';

export type Environment = 'dev' | 'test' | 'stage' | 'prod';

export interface Request {
  id: string;
  type: RequestType;
  title: string;
  status: RequestStatus;
  createdAt: string;
  updatedAt: string;
  stateMachine: StateMachineState;
  requiresTraining?: boolean;
  trainingCompleted?: boolean;
  environment?: Environment;
  requester_email?: string;
  lastError?: any;
  metadata?: Record<string, any>;
  conversation?: ChatMessage[];
}

export interface ProgressInfo {
  message: string;
  percent: number;
  timestamp: string;
}

export interface StateMachineState {
  currentState: string;
  states: State[];
  currentProgress?: ProgressInfo;
  parallelPaths?: ParallelPath[];
  completedStates?: string[];
  activeStates?: string[];
}

export interface State {
  id: string;
  name: string;
  isActive: boolean;
  isCompleted: boolean;
  isInitial: boolean;
  isFinal: boolean;
  completedAt?: string;
  startedAt?: string;
  facts?: {
    type: string;
    data: Record<string, any>;
    timestamp: string;
  }[];
}

// Legacy types kept for backwards compatibility (not used in new structure)
export interface ParallelPath {
  id: string;
  name: string;
  states: PathState[];
  required: boolean;
}

export interface PathState {
  id: string;
  name: string;
  status: 'pending' | 'active' | 'completed';
  order: number;
}

export interface BannerMessage {
  id: string;
  message: string;
  active: boolean;
  createdAt: string;
}

export type ApprovalType = 'platform_admin' | 'data_owner' | 'manager' | 'security';

export interface Approval {
  id: string;
  requestId: string;
  requestTitle: string;
  requestType: RequestType;
  approvalType: ApprovalType;
  requestedBy: string;
  requestedByEmail: string;
  status: 'pending' | 'approved' | 'rejected' | 'delegated' | 'superseded';
  createdAt: string;
  updatedAt: string;
  rejectionNote?: string;
  delegatedTo?: string;
  delegatedToEmail?: string;
  requestConversation?: ChatMessage[];
  /** Filtered workflow input parameters shown to the approver for context. */
  workflowParameters?: Record<string, any>;
  /** Explanation of why this approval was superseded by a parameter edit. */
  supersededNote?: string;
}

export interface ApprovalAction {
  approvalId: string;
  action: 'approve' | 'reject' | 'delegate' | 'edit';
  note?: string;
  delegatedToEmail?: string;
  /** New parameters to apply when action is 'edit'. */
  newParameters?: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  type: 'user' | 'agent';
  content: string;
  timestamp: Date;
}

export interface FollowUpQuestion {
  id: string;
  question: string;
  type: 'text' | 'select' | 'multi-select' | 'radio';
  options?: string[];
  required: boolean;
  answer?: string | string[];
}

export interface AgentAction {
  id: string;
  description: string;
  type: RequestType;
  approved: boolean;
}

export interface ConversationState {
  initialQuery: string;
  messages: ChatMessage[];
  currentQuestionIndex: number;
  followUpQuestions: FollowUpQuestion[];
  answers: Record<string, string | string[]>;
  agentActions: AgentAction[];
  showConfirmation: boolean;
  formRoute?: {
    path: string;
    title: string;
  };
  formPrefillData?: Record<string, any>;
  context?: {
    type: 'paas' | 'daas';
    title: string;
    initialPrompt?: string;
  };
}

export interface AssetLink {
  id: string;
  type: 'github' | 'confluence' | 'video' | 'documentation' | 'other';
  label: string;
  url: string;
}

export interface DesignPatternComment {
  id: string;
  designPatternId: string;
  author: string;
  authorEmail: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

export interface DesignPattern {
  id: string;
  title: string;
  description: string;
  author: string;
  authorEmail: string;
  team: string;
  tags: string[];
  githubUrl: string;
  assetLinks: AssetLink[];
  comments: DesignPatternComment[];
  createdAt: string;
  updatedAt: string;
  viewCount: number;
}

export interface Delegation {
  id: string;
  delegator_email: string;
  delegatee_email: string;
  start_date: string;
  end_date: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface DelegationCreate {
  delegatee_email: string;
  start_date: string;
  end_date: string;
}

export interface Branding {
  brand_name: string;
  brand_logo_url: string;
  brand_color_primary: string;
  brand_color_secondary: string;
}

export interface PromptDef {
  label: string;
  prompt: string;
}

export interface ReportSubscription {
  id: string;
  name: string;
  subscribers: string;
  schedule_cron: string;
  prompts: PromptDef[];
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string;
  created_at: string;
  updated_at: string | null;
}

export interface ReportSubscriptionCreate {
  name: string;
  subscribers: string;
  schedule_cron: string;
  prompts: PromptDef[];
  is_active: boolean;
}

export interface ReportSubscriptionUpdate {
  name?: string;
  subscribers?: string;
  schedule_cron?: string;
  prompts?: PromptDef[];
  is_active?: boolean;
}

export interface ExecutionSummary {
  id: string;
  title: string;
  status: string;
  created_at: string;
  completed_at: string | null;
}
