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
}

export interface State {
  id: string;
  name: string;
  isActive: boolean;
  isCompleted: boolean;
  isInitial: boolean;
  isFinal: boolean;
  completedAt?: string;
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
  status: 'pending' | 'approved' | 'rejected' | 'delegated';
  createdAt: string;
  updatedAt: string;
  rejectionNote?: string;
  delegatedTo?: string;
  delegatedToEmail?: string;
}

export interface ApprovalAction {
  approvalId: string;
  action: 'approve' | 'reject' | 'delegate';
  note?: string;
  delegatedToEmail?: string;
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

