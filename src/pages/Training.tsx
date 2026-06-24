import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { Card, CardContent } from '../components/ui/card';
import { Input } from '../components/ui/input';
import {
  Search, CheckCircle2, BookOpen, Unlock, ChevronRight, ChevronDown,
  PlayCircle, FileText, ExternalLink, Film,
} from 'lucide-react';
import {
  getTrainingStatus, recordTrainingConsumption, trainingMediaStreamUrl,
  type TrainingTrackFull, type TrainingCourseFull, type TrainingMedia,
} from '../services/api';

// Heartbeat cadence while a video is playing.
const HEARTBEAT_MS = 10_000;

function formatPct(value: number): number {
  return Math.round((value || 0) * 100);
}

/** Native HTML5 video with resume + throttled consumption heartbeats. */
function VideoPlayer({ media, onProgress }: { media: TrainingMedia; onProgress: (m: TrainingMedia, pct: number, completed: boolean) => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const lastSent = useRef(0);
  const resumed = useRef(false);

  const send = useCallback(async (position: number, total?: number) => {
    try {
      const res = await recordTrainingConsumption(media.id, position, total);
      onProgress(media, res.percent_complete, res.completed);
    } catch {
      // Best-effort: a dropped heartbeat just means slightly stale progress.
    }
  }, [media, onProgress]);

  const handleLoaded = () => {
    const el = ref.current;
    if (!el || resumed.current) return;
    resumed.current = true;
    const pos = media.consumption?.position_seconds || 0;
    // Resume a little before the saved point; don't jump to the very end.
    if (pos > 5 && el.duration && pos < el.duration - 2) {
      el.currentTime = pos;
    }
  };

  const handleTimeUpdate = () => {
    const el = ref.current;
    if (!el) return;
    const now = Date.now();
    if (now - lastSent.current >= HEARTBEAT_MS) {
      lastSent.current = now;
      void send(el.currentTime, el.duration || undefined);
    }
  };

  const handlePauseOrEnd = () => {
    const el = ref.current;
    if (!el) return;
    lastSent.current = Date.now();
    void send(el.currentTime, el.duration || undefined);
  };

  return (
    <video
      ref={ref}
      src={trainingMediaStreamUrl(media.id)}
      controls
      preload="metadata"
      className="w-full rounded-lg bg-black aspect-video"
      onLoadedMetadata={handleLoaded}
      onTimeUpdate={handleTimeUpdate}
      onPause={handlePauseOrEnd}
      onEnded={handlePauseOrEnd}
    />
  );
}

function MediaItem({ media, onProgress }: { media: TrainingMedia; onProgress: (m: TrainingMedia, pct: number, completed: boolean) => void }) {
  if (media.kind === 'video') {
    const pct = media.consumption?.percent_complete || 0;
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
          <Film className="w-4 h-4 text-primary" />
          {media.title}
          {media.consumption?.completed && <CheckCircle2 className="w-4 h-4 text-green-500" />}
        </div>
        <VideoPlayer media={media} onProgress={onProgress} />
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full bg-primary transition-all" style={{ width: `${formatPct(pct)}%` }} />
        </div>
        <div className="text-[11px] text-gray-400">{formatPct(pct)}% watched</div>
      </div>
    );
  }
  // Non-video resources: open/download via the stream endpoint.
  return (
    <a
      href={trainingMediaStreamUrl(media.id)}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
    >
      <FileText className="w-5 h-5 text-secondary" />
      <span className="text-sm font-medium text-gray-800 flex-1">{media.title}</span>
      <ExternalLink className="w-4 h-4 text-gray-400" />
    </a>
  );
}

export function Training() {
  const [searchTerm, setSearchTerm] = useState('');
  const [tracks, setTracks] = useState<TrainingTrackFull[]>([]);
  const [activeTab, setActiveTab] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const { tracks } = await getTrainingStatus() as { tracks: TrainingTrackFull[] };
        setTracks(tracks || []);
        if (tracks && tracks.length > 0) setActiveTab(tracks[0].id);
      } catch (error) {
        console.error('Failed to load training data:', error);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  // Patch local state when a heartbeat reports new progress, so progress bars
  // and completion checks update live without a full refetch.
  const handleProgress = useCallback((media: TrainingMedia, pct: number, completed: boolean) => {
    setTracks((prev) => prev.map((t) => ({
      ...t,
      courses: (t.courses || []).map((c) => {
        if (c.id !== media.course_id) return c;
        const updatedMedia = (c.media || []).map((m) =>
          m.id === media.id
            ? { ...m, consumption: { ...(m.consumption || { media_id: m.id, course_id: c.id, position_seconds: 0, view_count: 0 }), percent_complete: pct, completed } as TrainingMedia['consumption'] }
            : m,
        );
        const videos = updatedMedia.filter((m) => m.kind === 'video');
        const videoPcts = videos.map((m) => m.consumption?.percent_complete || 0);
        const progress = videoPcts.length ? videoPcts.reduce((a, b) => a + b, 0) / videoPcts.length : (c.progress || 0);
        const allDone = videos.length > 0 && videos.every((m) => m.consumption?.completed);
        return { ...c, media: updatedMedia, progress, status: allDone ? 'completed' : (progress > 0 ? 'in_progress' : c.status) };
      }),
    })));
  }, []);

  const filteredTracks = useMemo(() => {
    if (!searchTerm) return tracks;
    const s = searchTerm.toLowerCase();
    return tracks.filter((t) =>
      t.name.toLowerCase().includes(s) ||
      (t.courses || []).some((c) => c.title.toLowerCase().includes(s)));
  }, [tracks, searchTerm]);

  const activeTrack = useMemo(() =>
    filteredTracks.find((t) => t.id === activeTab) || filteredTracks[0],
    [filteredTracks, activeTab]);

  const toggleCourse = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Group a track's courses by section, preserving order.
  const sectionsFor = (track: TrainingTrackFull): { title: string; courses: TrainingCourseFull[] }[] => {
    const groups: Record<string, TrainingCourseFull[]> = {};
    const order: string[] = [];
    for (const c of track.courses || []) {
      const key = c.section || 'Courses';
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(c);
    }
    return order.map((k) => ({ title: k, courses: groups[k] }));
  };

  const StatusDot = ({ status }: { status?: string }) => {
    if (status === 'completed') return <div className="w-3 h-3 rounded-full bg-green-500 shadow-sm shadow-green-200" title="Completed" />;
    if (status === 'in_progress') return <div className="w-3 h-3 rounded-full bg-yellow-400 shadow-sm shadow-yellow-100" title="In progress" />;
    return <div className="w-3 h-3 rounded-full bg-gray-200 border border-gray-300" title="Not started" />;
  };

  return (
    <div className="space-y-8 pb-20 animate-in fade-in duration-500">
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
          {/* Tracks sidebar */}
          <div className="lg:col-span-1 space-y-2">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest px-2 mb-4">Learning Tracks</h3>
            {filteredTracks.map((track) => {
              const total = track.total_count ?? (track.courses?.length || 0);
              const done = track.completed_count ?? 0;
              const pct = total ? Math.round((done / total) * 100) : 0;
              return (
                <button
                  key={track.id}
                  onClick={() => setActiveTab(track.id)}
                  className={`w-full text-left px-4 py-3 rounded-xl transition-all flex items-center justify-between group ${activeTab === track.id
                    ? 'bg-primary text-white shadow-lg shadow-primary/20 scale-[1.02]'
                    : 'text-gray-600 hover:bg-gray-100'}`}
                >
                  <span className="font-semibold text-sm">{track.name}</span>
                  <span className={`text-[10px] font-bold ${activeTab === track.id ? 'text-white/80' : 'text-gray-400'}`}>{pct}%</span>
                </button>
              );
            })}
            {filteredTracks.length === 0 && (
              <p className="text-sm text-gray-400 px-2">No tracks found.</p>
            )}
          </div>

          {/* Content */}
          <div className="lg:col-span-3 space-y-6">
            {activeTrack ? (
              <Card className="border-0 shadow-sm bg-linear-to-br from-white to-gray-50 overflow-hidden">
                <div className="p-6 border-b border-gray-100 flex items-center justify-between bg-white">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center text-primary">
                      <BookOpen className="w-6 h-6" />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-gray-900">{activeTrack.name}</h2>
                      {activeTrack.description && <p className="text-xs text-gray-500 mt-0.5">{activeTrack.description}</p>}
                      <p className="text-xs text-gray-500 flex items-center gap-2 mt-1">
                        <CheckCircle2 className="w-3 h-3 text-green-500" />
                        {activeTrack.completed_count ?? 0} of {activeTrack.total_count ?? (activeTrack.courses?.length || 0)} courses completed
                      </p>
                    </div>
                  </div>
                </div>

                <CardContent className="p-0">
                  {sectionsFor(activeTrack).map((section) => (
                    <div key={section.title} className="border-b last:border-0 border-gray-100">
                      <div className="bg-gray-50/50 px-6 py-2 border-b border-gray-100 flex items-center justify-between">
                        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{section.title}</span>
                        <span className="text-[10px] text-gray-400">{section.courses.length} items</span>
                      </div>
                      {section.courses.map((course) => {
                        const isOpen = expanded.has(course.id);
                        const media = course.media || [];
                        return (
                          <div key={course.id} className="border-b last:border-0 border-gray-100">
                            <button
                              onClick={() => toggleCourse(course.id)}
                              className="w-full flex items-center py-3 px-6 hover:bg-gray-50 transition-colors text-left"
                            >
                              <div className="w-6 flex justify-center"><StatusDot status={course.status} /></div>
                              <div className="flex-1 min-w-0 px-3">
                                <div className="flex items-center gap-2">
                                  {course.external_url ? (
                                    <a
                                      href={course.external_url}
                                      target="_blank"
                                      rel="noreferrer"
                                      onClick={(e) => e.stopPropagation()}
                                      className="text-sm font-medium text-primary hover:underline truncate flex items-center gap-1"
                                    >
                                      {course.title}
                                      <ExternalLink className="w-3 h-3 shrink-0" />
                                    </a>
                                  ) : (
                                    <span className="text-sm font-medium text-gray-900 truncate">{course.title}</span>
                                  )}
                                  {course.course_type === 'Certification' && (
                                    <span className="bg-blue-50 text-blue-700 text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded border border-blue-100">Cert</span>
                                  )}
                                  {media.length > 0 && (
                                    <span className="flex items-center gap-1 text-[10px] text-gray-400"><PlayCircle className="w-3 h-3" />{media.length}</span>
                                  )}
                                </div>
                                <div className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-2">
                                  {course.course_type && <span>{course.course_type}</span>}
                                  {course.duration && (<><span className="text-gray-300">•</span><span>{course.duration}</span></>)}
                                  {(course.progress || 0) > 0 && course.status !== 'completed' && (
                                    <><span className="text-gray-300">•</span><span>{formatPct(course.progress || 0)}%</span></>
                                  )}
                                </div>
                              </div>
                              {course.unlocks && (
                                <div className="hidden sm:flex items-center gap-1.5 bg-purple-50 text-purple-700 px-3 py-1 rounded-full border border-purple-100 max-w-[200px] mr-2" title={course.unlocks}>
                                  <Unlock className="w-3 h-3 shrink-0" />
                                  <span className="text-[10px] font-bold uppercase truncate">Unlocks Access</span>
                                </div>
                              )}
                              {isOpen ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-300" />}
                            </button>

                            {isOpen && (
                              <div className="px-6 pb-5 pt-1 space-y-4 bg-gray-50/30">
                                {course.description && <p className="text-sm text-gray-600">{course.description}</p>}
                                {media.length === 0 ? (
                                  <p className="text-sm text-gray-400 italic">
                                    No media yet.{course.external_url ? ' Use the course link above to open it on the catalog.' : ''}
                                  </p>
                                ) : (
                                  <div className="space-y-5">
                                    {media.map((m) => (
                                      <MediaItem key={m.id} media={m} onProgress={handleProgress} />
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                      {section.courses.length === 0 && (
                        <p className="px-6 py-4 text-sm text-gray-400">No courses in this section.</p>
                      )}
                    </div>
                  ))}
                  {(activeTrack.courses?.length || 0) === 0 && (
                    <p className="px-6 py-10 text-center text-sm text-gray-400">This track has no courses yet.</p>
                  )}
                </CardContent>
              </Card>
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
