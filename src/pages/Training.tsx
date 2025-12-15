import { useState, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { useRequestStore } from '../stores/requestStore';
import { Search, CheckCircle2, Clock, BookOpen, TrendingUp, Unlock, ChevronDown, ChevronUp } from 'lucide-react';

interface Course {
  id: string;
  name: string;
  duration?: string;
  type: 'eLearning' | 'Accreditation' | 'SelfPaced' | 'Certification';
  unlocks?: string;
}

interface PersonaPath {
  persona: string;
  fundamentals: Course[];
  optionalLanguages?: Course[];
  associate: Course[];
  professional?: Course[];
}

interface CourseWithStatus extends Course {
  status: 'completed' | 'pending' | 'not_started';
  relatedRequestId?: string;
  relatedRequestTitle?: string;
}

interface TrackWithStatus extends PersonaPath {
  courses: CourseWithStatus[];
  completedCount: number;
  pendingCount: number;
  totalCount: number;
  status: 'complete' | 'action_required' | 'in_progress' | 'not_started';
}

const personaPaths: PersonaPath[] = [
  {
    persona: 'Business User',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
    ],
    associate: [],
    professional: [],
  },
  {
    persona: 'Data Analyst',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'sql-bi', name: 'Get Started with SQL Analytics and BI', duration: '3 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'sql-analytics', name: 'SQL Analytics on Databricks', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'ai-bi', name: 'AI/BI for Data Analysts', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-1', name: 'Cert - 90m', type: 'Certification', unlocks: 'Request advanced data access and build custom dashboards' },
    ],
    professional: [],
  },
  {
    persona: 'Data Engineer',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'get-started-de', name: 'Get Started with Databricks for Data Engineering', duration: '3 hrs', type: 'SelfPaced' },
    ],
    optionalLanguages: [
      { id: 'python-intro', name: 'Introduction to Python for Data Science and Data Engineering', duration: '12 hrs', type: 'SelfPaced' },
      { id: 'spark-prog', name: 'Apache Spark™ Programming with Databricks', duration: '12 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'data-ingestion', name: 'Data Ingestion with Lakeflow Connect', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'lakeflow-jobs', name: 'Deploy Workloads w/ Lakeflow Jobs', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'spark-pipelines', name: 'Build Data Pipelines w/ Lakeflow Spark Declarative Pipelines', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'unity-catalog', name: 'Data Management and Governance with Unity Catalog', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-1', name: 'Cert - 90m', type: 'Certification', unlocks: 'Create and manage data pipelines and catalogs' },
    ],
    professional: [
      { id: 'streaming', name: 'Databricks Streaming and Lakeflow Declarative Pipelines', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'data-privacy', name: 'Databricks Data Privacy', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'performance', name: 'Databricks Performance Optimization', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'asset-bundles', name: 'Automated Deployment with Databricks Asset Bundles', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-2', name: 'Cert - 2 hrs', type: 'Certification', unlocks: 'Provision workspaces and manage advanced data engineering workflows' },
    ],
  },
  {
    persona: 'Data Science & ML',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'get-started-ml', name: 'Get Started with Databricks for Machine Learning', duration: '3 hrs', type: 'SelfPaced' },
    ],
    optionalLanguages: [
      { id: 'python-intro', name: 'Introduction to Python for Data Science and Data Engineering', duration: '12 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'data-prep', name: 'Data Preparation for Machine Learning', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'model-dev', name: 'Machine Learning Model Development', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'model-deploy', name: 'Machine Learning Model Deployment', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'mlops', name: 'Machine Learning Operations', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-1', name: 'Cert - 90m', type: 'Certification', unlocks: 'Deploy ML models and access ML platform features' },
    ],
    professional: [
      { id: 'ml-scale', name: 'Machine Learning at Scale', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'advanced-mlops', name: 'Advanced Machine Learning Operations', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-2', name: 'Cert - 2 hrs', type: 'Certification', unlocks: 'Manage production ML workflows and model registry' },
    ],
  },
  {
    persona: 'Generative AI',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'gen-ai-fund', name: 'Generative AI Fundamentals', duration: '2 hrs', type: 'eLearning' },
      { id: 'accred-1', name: 'Accred.', type: 'Accreditation' },
      { id: 'accred-2', name: 'Accred.', type: 'Accreditation' },
      { id: 'get-started-genai', name: 'Get Started with Databricks for Gen AI', duration: '3 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'gen-ai-solution', name: 'Generative AI Solution Development', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'gen-ai-app-dev', name: 'Gen AI Application Development', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'gen-ai-eval', name: 'Gen AI Application Evaluation and Governance', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'gen-ai-deploy', name: 'Gen AI Application Deployment and Monitoring', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'cert-1', name: 'Cert - 90m', type: 'Certification', unlocks: 'Build and deploy LLM-powered applications' },
    ],
    professional: [],
  },
  {
    persona: 'Platform Admin',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'get-started-admin', name: 'Get Started with Databricks Platform Admin', duration: '3 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'platform-admin-plan', name: 'Platform Administrator Learning Plan', duration: '8 hrs', type: 'SelfPaced' },
      { id: 'accred-2', name: 'Accreditation', type: 'Accreditation' },
    ],
    professional: [],
  },
  {
    persona: 'Data Warehousing',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'get-started-dw', name: 'Get Started with Data Warehousing', duration: '3 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'data-warehousing', name: 'Data Warehousing with Databricks', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'sql-procedural', name: 'SQL Programming and Procedural Logic', duration: '3 hrs', type: 'SelfPaced' },
      { id: 'data-modeling', name: 'Data Modeling Strategies', duration: '3 hrs', type: 'SelfPaced' },
    ],
    professional: [],
  },
  {
    persona: 'Data Architect',
    fundamentals: [
      { id: 'fundamentals', name: 'Databricks Fundamentals', duration: '1 hr', type: 'eLearning' },
      { id: 'accred-1', name: 'Accreditation', type: 'Accreditation' },
      { id: 'get-started-arch', name: 'Get Started with Lakehouse Architecture', duration: '2 hrs', type: 'SelfPaced' },
    ],
    associate: [
      { id: 'data-modeling', name: 'Data Modeling Strategies', duration: '3 hrs', type: 'SelfPaced' },
    ],
    professional: [],
  },
];

// Mock completed courses (in production, this would come from an API)
const completedCourseIds = new Set(['fundamentals', 'accred-1']);

// Demo pending courses for demonstration purposes
const pendingCourseIds = new Set(['sql-bi', 'get-started-de', 'get-started-ml', 'data-ingestion', 'sql-analytics']);

// Helper function to determine course status based on requests
function getCourseStatus(
  courseId: string,
  requests: any[]
): { status: 'completed' | 'pending' | 'not_started'; relatedRequestId?: string; relatedRequestTitle?: string } {
  // Check if course is completed
  if (completedCourseIds.has(courseId)) {
    return { status: 'completed' };
  }

  // Demo: Check if course is in pending list
  if (pendingCourseIds.has(courseId)) {
    const pendingRequest = requests.find(
      (req) =>
        req.requiresTraining &&
        !req.trainingCompleted &&
        (req.status === 'training_pending' || req.status === 'pending' || req.status === 'manager_approval')
    );
    return {
      status: 'pending',
      relatedRequestId: pendingRequest?.id || 'demo-request',
      relatedRequestTitle: pendingRequest?.title || 'Demo: Workspace Provision Request',
    };
  }

  // Check if course is required for any pending request
  // For workspace_provision requests, typically require fundamentals + accreditation
  const pendingRequest = requests.find(
    (req) =>
      req.requiresTraining &&
      !req.trainingCompleted &&
      (req.status === 'training_pending' || req.status === 'pending' || req.status === 'manager_approval')
  );

  if (pendingRequest && (courseId === 'fundamentals' || courseId === 'accred-1')) {
    return {
      status: 'pending',
      relatedRequestId: pendingRequest.id,
      relatedRequestTitle: pendingRequest.title,
    };
  }

  return { status: 'not_started' };
}

function highlightText(text: string, searchTerm: string): React.ReactNode {
  if (!searchTerm) return text;
  
  const regex = new RegExp(`(${searchTerm})`, 'gi');
  const parts = text.split(regex);
  
  return parts.map((part, index) =>
    regex.test(part) ? (
      <mark key={index} className="bg-yellow-200 text-yellow-900 px-0.5 rounded">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

export function Training() {
  const { requests } = useRequestStore();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedPersona, setSelectedPersona] = useState<string>('all');
  const [selectedStatus, setSelectedStatus] = useState<string>('all');
  // Initialize all tracks as expanded by default
  const [expandedTracks, setExpandedTracks] = useState<Set<string>>(
    new Set(personaPaths.map((p) => p.persona))
  );

  // Process tracks with status information
  const tracksWithStatus: TrackWithStatus[] = useMemo(() => {
    return personaPaths.map((path) => {
      const allCourses: Course[] = [
        ...path.fundamentals,
        ...(path.optionalLanguages || []),
        ...path.associate,
        ...(path.professional || []),
      ];

      const coursesWithStatus: CourseWithStatus[] = allCourses.map((course) => {
        const statusInfo = getCourseStatus(course.id, requests);
        return {
          ...course,
          status: statusInfo.status,
          relatedRequestId: statusInfo.relatedRequestId,
          relatedRequestTitle: statusInfo.relatedRequestTitle,
        };
      });

      const completedCount = coursesWithStatus.filter((c) => c.status === 'completed').length;
      const pendingCount = coursesWithStatus.filter((c) => c.status === 'pending').length;

      // Determine overall track status
      let status: 'complete' | 'action_required' | 'in_progress' | 'not_started';
      if (completedCount === coursesWithStatus.length) {
        status = 'complete';
      } else if (pendingCount > 0) {
        status = 'action_required';
      } else if (completedCount > 0) {
        status = 'in_progress';
      } else {
        status = 'not_started';
      }

      return {
        ...path,
        courses: coursesWithStatus,
        completedCount,
        pendingCount,
        totalCount: coursesWithStatus.length,
        status,
      };
    });
  }, [requests]);

  // Filter tracks
  const filteredTracks = useMemo(() => {
    let filtered = tracksWithStatus;

    // Filter by persona
    if (selectedPersona !== 'all') {
      filtered = filtered.filter((track) => track.persona === selectedPersona);
    }

    // Filter by status - only show tracks that have courses matching the status filter
    if (selectedStatus !== 'all') {
      filtered = filtered.map((track) => ({
        ...track,
        courses: track.courses.filter((course) => {
          // Also filter by search term if present
          if (searchTerm) {
            const lowerSearch = searchTerm.toLowerCase();
            const matchesSearch =
              course.name.toLowerCase().includes(lowerSearch) ||
              track.persona.toLowerCase().includes(lowerSearch) ||
              course.type.toLowerCase().includes(lowerSearch);
            return course.status === selectedStatus && matchesSearch;
          }
          return course.status === selectedStatus;
        }),
      })).filter((track) => track.courses.length > 0);
    } else if (searchTerm) {
      // If only search term, filter courses within tracks
      const lowerSearch = searchTerm.toLowerCase();
      filtered = filtered.map((track) => ({
        ...track,
        courses: track.courses.filter(
          (course) =>
            course.name.toLowerCase().includes(lowerSearch) ||
            track.persona.toLowerCase().includes(lowerSearch) ||
            course.type.toLowerCase().includes(lowerSearch)
        ),
      })).filter((track) => track.courses.length > 0);
    }

    return filtered;
  }, [tracksWithStatus, searchTerm, selectedPersona, selectedStatus]);

  const getStatusColor = (status: CourseWithStatus['status']) => {
    switch (status) {
      case 'completed':
        return 'bg-green-100 border-green-300 text-green-900';
      case 'pending':
        return 'bg-yellow-100 border-yellow-300 text-yellow-900';
      case 'not_started':
        return 'bg-gray-50 border-gray-200 text-gray-700';
      default:
        return 'bg-gray-50 border-gray-200 text-gray-700';
    }
  };

  const getStatusIcon = (status: CourseWithStatus['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="w-4 h-4" />;
      case 'pending':
        return <Clock className="w-4 h-4" />;
      case 'not_started':
        return <BookOpen className="w-4 h-4" />;
    }
  };

  const CourseCard = ({ course }: { course: CourseWithStatus }) => (
    <div
      className={`${getStatusColor(course.status)} border rounded-lg p-3 transition-all hover:shadow-md`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 flex-1">
          {getStatusIcon(course.status)}
          <h4 className="font-semibold text-sm">
            {highlightText(course.name, searchTerm)}
          </h4>
        </div>
      </div>
      
      <div className="space-y-1 text-xs">
        {course.duration && (
          <div className="opacity-80">Duration: {course.duration}</div>
        )}
        {course.status === 'pending' && course.relatedRequestTitle && (
          <div className="mt-2 pt-2 border-t border-current/20">
            <div className="font-medium">Required for:</div>
            <div className="text-xs opacity-90">{course.relatedRequestTitle}</div>
          </div>
        )}
        {course.unlocks && (
          <div className="mt-3 pt-3 border-t-2 border-current/30 bg-white/40 rounded-md p-2">
            <div className="flex items-start gap-2">
              <Unlock className="w-4 h-4 text-purple-700 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-bold text-xs mb-1 text-purple-900">Unlocks</div>
                <div className="text-xs font-medium leading-relaxed">
                  {course.unlocks}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  const uniquePersonas = personaPaths.map((p) => p.persona);

  const toggleTrack = (persona: string) => {
    setExpandedTracks((prev) => {
      const next = new Set(prev);
      if (next.has(persona)) {
        next.delete(persona);
      } else {
        next.add(persona);
      }
      return next;
    });
  };

  const isTrackExpanded = (persona: string) => expandedTracks.has(persona);

  return (
    <div className="space-y-6">
      {/* Header and Explanation */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-4">Training & Development</h1>
        <Card className="bg-blue-50 border-blue-200">
          <CardContent className="pt-6">
            <div className="space-y-3 text-sm text-gray-700">
              <p className="font-semibold text-base text-gray-900">Your Learning Tracks</p>
              <p>
                Training is organized by learning tracks aligned to different roles and personas. 
                Each track provides a structured path from fundamentals through advanced topics. 
                Track your progress, see what's required for your pending requests, and discover 
                courses that align with your role.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
                <div className="flex items-start gap-2">
                  <div className="w-5 h-5 rounded bg-green-100 border border-green-300 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <CheckCircle2 className="w-3 h-3 text-green-700" />
                  </div>
                  <div>
                    <div className="font-semibold text-gray-900">Completed</div>
                    <div className="text-xs text-gray-600">Courses you've finished</div>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <div className="w-5 h-5 rounded bg-yellow-100 border border-yellow-300 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Clock className="w-3 h-3 text-yellow-700" />
                  </div>
                  <div>
                    <div className="font-semibold text-gray-900">Pending</div>
                    <div className="text-xs text-gray-600">Required for your active requests</div>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <div className="w-5 h-5 rounded bg-gray-50 border border-gray-200 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <BookOpen className="w-3 h-3 text-gray-600" />
                  </div>
                  <div>
                    <div className="font-semibold text-gray-900">Available</div>
                    <div className="text-xs text-gray-600">Not started, not yet required</div>
                  </div>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-blue-200">
                <div className="flex items-start gap-2">
                  <div className="w-5 h-5 rounded bg-purple-100 border border-purple-300 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Unlock className="w-3 h-3 text-purple-700" />
                  </div>
                  <div className="flex-1">
                    <div className="font-semibold text-gray-900 mb-1">Unlocks</div>
                    <div className="text-xs text-gray-600">
                      Some courses unlock new capabilities or permissions. When you see an "Unlocks" section on a course card, 
                      completing that course will grant you the ability to make specific types of requests or access advanced features.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Search and Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="space-y-4">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5" />
              <Input
                type="text"
                placeholder="Search courses by name, track, or type..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="pl-10"
              />
            </div>
            <div className="flex flex-wrap gap-3">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Filter by Learning Track
                </label>
                <select
                  value={selectedPersona}
                  onChange={(e) => setSelectedPersona(e.target.value)}
                  className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  <option value="all">All Learning Tracks</option>
                  {uniquePersonas.map((persona) => (
                    <option key={persona} value={persona}>
                      {persona}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Filter by Status
                </label>
                <select
                  value={selectedStatus}
                  onChange={(e) => setSelectedStatus(e.target.value)}
                  className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  <option value="all">All Status</option>
                  <option value="completed">Completed</option>
                  <option value="pending">Pending</option>
                  <option value="not_started">Not Started</option>
                </select>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Learning Tracks */}
      {filteredTracks.length > 0 ? (
        filteredTracks.map((track) => {
          const trackExpanded = isTrackExpanded(track.persona);
          return (
            <Card key={track.persona} className="overflow-hidden">
              <CardHeader 
                className="bg-gradient-to-r from-gray-50 to-gray-100 border-b border-gray-200 cursor-pointer hover:from-gray-100 hover:to-gray-200 transition-colors"
                onClick={() => toggleTrack(track.persona)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 flex-1">
                    <TrendingUp className="w-6 h-6 text-gray-600" />
                    <div className="flex-1">
                      <div className="flex items-center gap-3">
                        <CardTitle className="text-xl text-gray-900">{track.persona} Learning Track</CardTitle>
                        {/* Status Badge */}
                        <div className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                          track.status === 'complete' 
                            ? 'bg-green-100 text-green-800 border border-green-300'
                            : track.status === 'action_required'
                            ? 'bg-yellow-100 text-yellow-800 border border-yellow-300'
                            : track.status === 'in_progress'
                            ? 'bg-blue-100 text-blue-800 border border-blue-300'
                            : 'bg-gray-100 text-gray-800 border border-gray-300'
                        }`}>
                          {track.status === 'complete' 
                            ? '✓ Complete'
                            : track.status === 'action_required'
                            ? '⚠ Action Required'
                            : track.status === 'in_progress'
                            ? '→ In Progress'
                            : '○ Not Started'}
                        </div>
                      </div>
                      <div className="flex items-center gap-4 mt-1 text-sm text-gray-600">
                        <span>
                          <span className="font-semibold text-green-700">{track.completedCount}</span> completed
                        </span>
                        <span className={track.pendingCount > 0 ? 'font-semibold' : ''}>
                          <span className={`font-semibold ${track.pendingCount > 0 ? 'text-yellow-700' : 'text-gray-600'}`}>
                            {track.pendingCount}
                          </span>{' '}
                          {track.pendingCount === 1 ? 'pending' : 'pending'} required for active tasks
                        </span>
                        <span>
                          <span className="font-semibold">{track.totalCount}</span> total courses
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="ml-4">
                    {trackExpanded ? (
                      <ChevronUp className="w-5 h-5 text-gray-600" />
                    ) : (
                      <ChevronDown className="w-5 h-5 text-gray-600" />
                    )}
                  </div>
                </div>
              </CardHeader>
              {trackExpanded && (
                <CardContent className="pt-6">
              {/* Fundamentals */}
              {track.fundamentals.length > 0 && (
                <div className="mb-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 uppercase tracking-wide">
                    Fundamentals & Onboarding
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {track.fundamentals
                      .map((courseId) => track.courses.find((c) => c.id === courseId.id))
                      .filter((course): course is CourseWithStatus => course !== undefined)
                      .map((course) => (
                        <CourseCard key={course.id} course={course} />
                      ))}
                  </div>
                </div>
              )}

              {/* Optional Languages */}
              {track.optionalLanguages && track.optionalLanguages.length > 0 && (
                <div className="mb-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 uppercase tracking-wide">
                    Optional Languages
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {track.optionalLanguages
                      .map((courseId) => track.courses.find((c) => c.id === courseId.id))
                      .filter((course): course is CourseWithStatus => course !== undefined)
                      .map((course) => (
                        <CourseCard key={course.id} course={course} />
                      ))}
                  </div>
                </div>
              )}

              {/* Associate */}
              {track.associate.length > 0 && (
                <div className="mb-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 uppercase tracking-wide">
                    Associate / Intermediate
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {track.associate
                      .map((courseId) => track.courses.find((c) => c.id === courseId.id))
                      .filter((course): course is CourseWithStatus => course !== undefined)
                      .map((course) => (
                        <CourseCard key={course.id} course={course} />
                      ))}
                  </div>
                </div>
              )}

              {/* Professional */}
              {track.professional && track.professional.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 uppercase tracking-wide">
                    Professional / Advanced
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {track.professional
                      .map((courseId) => track.courses.find((c) => c.id === courseId.id))
                      .filter((course): course is CourseWithStatus => course !== undefined)
                      .map((course) => (
                        <CourseCard key={course.id} course={course} />
                      ))}
                  </div>
                </div>
              )}
                </CardContent>
              )}
            </Card>
          );
        })
      ) : (
        <Card>
          <CardContent className="pt-6 text-center py-12">
            <p className="text-gray-500">No courses match your search criteria.</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
