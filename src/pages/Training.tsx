import { useState, useMemo, useEffect } from 'react';
import { Card, CardContent } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Search, CheckCircle2, BookOpen, Unlock, ChevronRight } from 'lucide-react';
import { getTrainingStatus } from '../services/api';

interface Course {
  id: string;
  name: string;
  duration?: string;
  type: string;
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
}

interface TrackWithStatus extends PersonaPath {
  courses: CourseWithStatus[];
  completedCount: number;
  totalCount: number;
}

export function Training() {
  const [searchTerm, setSearchTerm] = useState('');
  const [allTracks, setAllTracks] = useState<PersonaPath[]>([]);
  const [activeTab, setActiveTab] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [completedCourseIds, setCompletedCourseIds] = useState<Set<string>>(new Set());

  // Mock pending courses (for demonstration)
  const pendingCourseIds = new Set(['sql-bi', 'get-started-de', 'get-started-ml', 'data-ingestion', 'sql-analytics']);

  // Fetch training data
  useEffect(() => {
    const loadTrainingData = async () => {
      try {
        setLoading(true);
        // Fetch both tracks and status
        const { tracks, completed_codes } = await getTrainingStatus() as { tracks: PersonaPath[], completed_codes: string[] };

        setAllTracks(tracks || []);

        // Update loaded completed courses
        setCompletedCourseIds(new Set(completed_codes || []));

        if (tracks && tracks.length > 0) {
          setActiveTab(tracks[0].persona);
        }
      } catch (error) {
        console.error('Failed to load training data:', error);
      } finally {
        setLoading(false);
      }
    };
    loadTrainingData();
  }, []);

  // Process tracks with status
  const tracksWithStatus: TrackWithStatus[] = useMemo(() => {
    return allTracks.map((path) => {
      const getStatus = (courseId: string) => {
        if (completedCourseIds.has(courseId)) return 'completed';
        if (pendingCourseIds.has(courseId)) return 'pending';
        return 'not_started';
      };

      const groupToCourses = (group?: Course[]) =>
        (group || []).map(c => ({ ...c, status: getStatus(c.id) as CourseWithStatus['status'] }));

      const courses = [
        ...groupToCourses(path.fundamentals),
        ...groupToCourses(path.optionalLanguages),
        ...groupToCourses(path.associate),
        ...groupToCourses(path.professional)
      ];

      return {
        ...path,
        courses,
        completedCount: courses.filter(c => c.status === 'completed').length,
        totalCount: courses.length
      };
    });
  }, [allTracks, completedCourseIds, pendingCourseIds]);

  // Combined filtering
  const filteredTracks = useMemo(() => {
    if (!searchTerm) return tracksWithStatus;
    const lowerSearch = searchTerm.toLowerCase();
    return tracksWithStatus.filter(track =>
      track.persona.toLowerCase().includes(lowerSearch) ||
      track.courses.some(c => c.name.toLowerCase().includes(lowerSearch))
    );
  }, [tracksWithStatus, searchTerm]);

  const activeTrack = useMemo(() =>
    filteredTracks.find(t => t.persona === activeTab) || filteredTracks[0],
    [filteredTracks, activeTab]);

  const StatusCircle = ({ status }: { status: CourseWithStatus['status'] }) => {
    if (status === 'completed') return <div className="w-3 h-3 rounded-full bg-green-500 shadow-sm shadow-green-200" title="Completed" />;
    if (status === 'pending') return <div className="w-3 h-3 rounded-full bg-yellow-400 animate-pulse shadow-sm shadow-yellow-100" title="Pending required" />;
    return <div className="w-3 h-3 rounded-full bg-gray-200 border border-gray-300" title="Not started" />;
  };

  const CourseRow = ({ course }: { course: CourseWithStatus }) => (
    <div className="flex items-center py-3 px-4 hover:bg-gray-50 transition-colors border-b last:border-0">
      <div className="w-8 flex justify-center">
        <StatusCircle status={course.status} />
      </div>
      <div className="flex-1 min-w-0 px-4">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium truncate ${course.status === 'completed' ? 'text-gray-500' : 'text-gray-900'}`}>
            {course.name}
          </span>
          {course.type === 'Certification' && (
            <span className="bg-blue-50 text-blue-700 text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded border border-blue-100">
              Cert
            </span>
          )}
        </div>
        <div className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-2">
          <span>{course.type}</span>
          {course.duration && (
            <>
              <span className="text-gray-300">•</span>
              <span>{course.duration}</span>
            </>
          )}
        </div>
      </div>
      <div className="w-1/3 flex justify-end items-center gap-3">
        {course.unlocks && (
          <div className="flex items-center gap-1.5 bg-purple-50 text-purple-700 px-3 py-1 rounded-full border border-purple-100 max-w-[200px]" title={course.unlocks}>
            <Unlock className="w-3 h-3 flex-shrink-0" />
            <span className="text-[10px] font-bold uppercase truncate">Unlocks Access</span>
          </div>
        )}
        <ChevronRight className="w-4 h-4 text-gray-300" />
      </div>
    </div>
  );

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-in fade-in duration-500">
      {/* Search Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div className="space-y-1">
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">Learning & Development</h1>
          <p className="text-gray-500 text-sm">Follow structured paths to unlock advanced features and platform capabilities.</p>
        </div>
        <div className="relative w-full md:w-80">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
          <Input
            placeholder="Search tracks or courses..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 bg-white border-gray-200 focus:ring-primary h-10 shadow-sm"
          />
        </div>
      </div>

      {loading ? (
        <div className="py-20 text-center space-y-4">
          <div className="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-gray-500">Loading curriculum...</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          {/* Tabs Sidebar */}
          <div className="lg:col-span-1 space-y-2">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest px-2 mb-4">Learning Tracks</h3>
            {filteredTracks.map((track) => (
              <button
                key={track.persona}
                onClick={() => setActiveTab(track.persona)}
                className={`w-full text-left px-4 py-3 rounded-xl transition-all flex items-center justify-between group ${activeTab === track.persona
                  ? 'bg-primary text-white shadow-lg shadow-primary/20 scale-[1.02]'
                  : 'text-gray-600 hover:bg-gray-100'
                  }`}
              >
                <span className="font-semibold text-sm">{track.persona}</span>
                <div className={`flex items-center gap-1.5 ${activeTab === track.persona ? 'text-white/80' : 'text-gray-400'}`}>
                  <span className="text-[10px] font-bold">
                    {Math.round((track.completedCount / track.totalCount) * 100)}%
                  </span>
                </div>
              </button>
            ))}
          </div>

          {/* Content Area */}
          <div className="lg:col-span-3 space-y-6">
            {activeTrack ? (
              <div className="space-y-8">
                {/* Track Overview Card */}
                <Card className="border-0 shadow-sm bg-gradient-to-br from-white to-gray-50 overflow-hidden">
                  <div className="p-6 border-b border-gray-100 flex items-center justify-between bg-white">
                    <div className="flex items-center gap-4">
                      <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center text-primary">
                        <BookOpen className="w-6 h-6" />
                      </div>
                      <div>
                        <h2 className="text-xl font-bold text-gray-900">{activeTrack.persona} Curriculum</h2>
                        <p className="text-xs text-gray-500 flex items-center gap-2 mt-1">
                          <CheckCircle2 className="w-3 h-3 text-green-500" />
                          {activeTrack.completedCount} of {activeTrack.totalCount} milestones completed
                        </p>
                      </div>
                    </div>
                  </div>

                  <CardContent className="p-0">
                    <div className="flex flex-col">
                      {/* Sub-sections */}
                      {[
                        { title: 'Fundamentals', courses: activeTrack.fundamentals },
                        { title: 'Optional Languages', courses: activeTrack.optionalLanguages },
                        { title: 'Associate Milestones', courses: activeTrack.associate },
                        { title: 'Professional Excellence', courses: activeTrack.professional }
                      ].map(section => (
                        section.courses && section.courses.length > 0 && (
                          <div key={section.title} className="border-b last:border-0 border-gray-100">
                            <div className="bg-gray-50/50 px-6 py-2 border-b border-gray-100 flex items-center justify-between">
                              <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{section.title}</span>
                              <span className="text-[10px] text-gray-400">{section.courses.length} items</span>
                            </div>
                            <div>
                              {activeTrack.courses
                                .filter(c => section.courses?.some(sc => sc.id === c.id))
                                .map(course => (
                                  <CourseRow key={`${activeTrack.persona}-${course.id}`} course={course} />
                                ))}
                            </div>
                          </div>
                        )
                      ))}
                    </div>
                  </CardContent>
                </Card>

                {/* Legend / Info */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 pt-4 border-t border-gray-100">
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-lg bg-green-50 flex items-center justify-center flex-shrink-0">
                      <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
                    </div>
                    <div>
                      <p className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">Completed</p>
                      <p className="text-xs text-gray-600">Already credentialed</p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-lg bg-yellow-50 flex items-center justify-center flex-shrink-0">
                      <div className="w-2.5 h-2.5 rounded-full bg-yellow-400" />
                    </div>
                    <div>
                      <p className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">Required</p>
                      <p className="text-xs text-gray-600">Locks active requests</p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center flex-shrink-0">
                      <Unlock className="w-4 h-4 text-purple-600" />
                    </div>
                    <div>
                      <p className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">Unlocker</p>
                      <p className="text-xs text-gray-600">Grants system access</p>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="py-20 text-center">
                <p className="text-gray-400">No tracks found matching your search.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
