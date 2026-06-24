import { useEffect, useMemo, useRef, useState } from 'react';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  GraduationCap, Plus, Trash2, Loader2, Save, Upload, RefreshCw, Pencil, X,
  Film, FileText, ChevronRight, ChevronDown, BarChart3, Link2, FileUp,
} from 'lucide-react';
import {
  adminListTrainingTracks, adminCreateTrainingTrack, adminUpdateTrainingTrack, adminDeleteTrainingTrack,
  adminCreateTrainingCourse, adminUpdateTrainingCourse, adminDeleteTrainingCourse,
  adminUploadTrainingMedia, adminDeleteTrainingMedia, adminSyncTrainingCatalog,
  adminTrainingConsumptionAnalytics, uploadTrainingData,
  type TrainingTrackFull, type TrainingCourseFull, type CourseConsumptionRow, type CatalogSyncResult,
} from '../../services/api';

const inputClass = 'w-full border border-gray-300 rounded-md h-9 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary';
const labelClass = 'text-[11px] font-bold text-gray-500 uppercase tracking-wider';

interface CourseForm {
  title: string; description: string; course_code: string; external_url: string;
  section: string; course_type: string; duration: string; unlocks: string; status: string;
}

const emptyCourseForm: CourseForm = {
  title: '', description: '', course_code: '', external_url: '',
  section: 'fundamentals', course_type: '', duration: '', unlocks: '', status: 'published',
};

export function TrainingAdmin() {
  const [tracks, setTracks] = useState<TrainingTrackFull[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [activeTrackId, setActiveTrackId] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [trackForm, setTrackForm] = useState<{ name: string; description: string; persona: string } | null>(null);
  const [editingTrackId, setEditingTrackId] = useState<string | null>(null);

  const [courseForm, setCourseForm] = useState<CourseForm | null>(null);
  const [editingCourseId, setEditingCourseId] = useState<string | null>(null);

  const [expandedCourse, setExpandedCourse] = useState<Set<string>>(new Set());
  const [analytics, setAnalytics] = useState<CourseConsumptionRow[]>([]);
  const [showAnalytics, setShowAnalytics] = useState(false);

  const csvInput = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      setLoading(true);
      const data = await adminListTrainingTracks();
      setTracks(data);
      if (data.length && !activeTrackId) setActiveTrackId(data[0].id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tracks');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const activeTrack = useMemo(() => tracks.find((t) => t.id === activeTrackId), [tracks, activeTrackId]);

  const flash = (msg: string) => { setNotice(msg); setTimeout(() => setNotice(null), 4000); };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Something went wrong');

  // --- Tracks ---
  const saveTrack = async () => {
    if (!trackForm?.name.trim()) return;
    setBusy(true); setError(null);
    try {
      if (editingTrackId) {
        await adminUpdateTrainingTrack(editingTrackId, trackForm);
      } else {
        const created = await adminCreateTrainingTrack(trackForm);
        setActiveTrackId(created.id);
      }
      setTrackForm(null); setEditingTrackId(null);
      await load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const removeTrack = async (id: string) => {
    if (!confirm('Delete this track and all its courses + media? This cannot be undone.')) return;
    setBusy(true); setError(null);
    try {
      await adminDeleteTrainingTrack(id);
      if (activeTrackId === id) setActiveTrackId('');
      await load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  // --- Courses ---
  const saveCourse = async () => {
    if (!courseForm?.title.trim() || !activeTrack) return;
    setBusy(true); setError(null);
    try {
      if (editingCourseId) {
        await adminUpdateTrainingCourse(editingCourseId, courseForm);
      } else {
        await adminCreateTrainingCourse(activeTrack.id, courseForm);
      }
      setCourseForm(null); setEditingCourseId(null);
      await load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const removeCourse = async (id: string) => {
    if (!confirm('Delete this course and its media?')) return;
    setBusy(true); setError(null);
    try { await adminDeleteTrainingCourse(id); await load(); } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const editCourse = (c: TrainingCourseFull) => {
    setEditingCourseId(c.id);
    setCourseForm({
      title: c.title, description: c.description || '', course_code: c.course_code || '',
      external_url: c.external_url || '', section: c.section || 'fundamentals',
      course_type: c.course_type || '', duration: c.duration || '', unlocks: c.unlocks || '',
      status: c.status || 'published',
    });
  };

  // --- Media ---
  const uploadMedia = async (courseId: string, file: File, kind: string) => {
    setBusy(true); setError(null);
    try {
      await adminUploadTrainingMedia(courseId, file, file.name, kind);
      flash(`Uploaded ${file.name}`);
      await load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const removeMedia = async (id: string) => {
    if (!confirm('Delete this media file?')) return;
    setBusy(true); setError(null);
    try { await adminDeleteTrainingMedia(id); await load(); } catch (e) { fail(e); } finally { setBusy(false); }
  };

  // --- Catalog + analytics + CSV ---
  const syncCatalog = async () => {
    setBusy(true); setError(null);
    try {
      const res: CatalogSyncResult = await adminSyncTrainingCatalog();
      flash(`${res.note || 'Sync complete'} (added ${res.stats.added}, updated ${res.stats.updated})`);
      await load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const loadAnalytics = async () => {
    setShowAnalytics((v) => !v);
    if (!showAnalytics) {
      try { setAnalytics(await adminTrainingConsumptionAnalytics()); } catch (e) { fail(e); }
    }
  };

  const onCsv = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const res = await uploadTrainingData(file);
      flash(`CSV processed: +${res.stats.added} added, ${res.stats.updated} updated`);
    } catch (e) { fail(e); } finally { setBusy(false); if (csvInput.current) csvInput.current.value = ''; }
  };

  const toggleCourse = (id: string) => setExpandedCourse((prev) => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next;
  });

  return (
    <div className="space-y-6 pb-20">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-2xl bg-primary/10 flex items-center justify-center text-primary">
            <GraduationCap className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Training Studio</h1>
            <p className="text-sm text-gray-500">Author learning tracks, courses, and upload media. Track consumption.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={syncCatalog} disabled={busy}>
            <RefreshCw className={`w-4 h-4 mr-2 ${busy ? 'animate-spin' : ''}`} /> Sync from Catalog
          </Button>
          <Button variant="outline" size="sm" onClick={loadAnalytics}>
            <BarChart3 className="w-4 h-4 mr-2" /> {showAnalytics ? 'Hide' : 'Consumption'}
          </Button>
          <Button variant="outline" size="sm" onClick={() => csvInput.current?.click()} disabled={busy}>
            <FileUp className="w-4 h-4 mr-2" /> Academy CSV
          </Button>
          <input ref={csvInput} type="file" accept=".csv" className="hidden" onChange={(e) => onCsv(e.target.files?.[0])} />
        </div>
      </div>

      {error && (
        <div className="flex items-center justify-between bg-red-50 border border-red-200 text-red-700 px-4 py-2 rounded-md text-sm">
          {error}<button onClick={() => setError(null)}><X className="w-4 h-4" /></button>
        </div>
      )}
      {notice && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-2 rounded-md text-sm">{notice}</div>
      )}

      {showAnalytics && (
        <Card>
          <CardContent className="p-0">
            <div className="px-4 py-3 border-b text-sm font-semibold text-gray-700">Course Consumption</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-[11px] uppercase tracking-wider">
                  <tr><th className="text-left px-4 py-2">Course</th><th className="text-right px-4 py-2">Learners</th><th className="text-right px-4 py-2">Avg %</th><th className="text-right px-4 py-2">Media Completions</th></tr>
                </thead>
                <tbody>
                  {analytics.map((r) => (
                    <tr key={r.course_id} className="border-t border-gray-100">
                      <td className="px-4 py-2 text-gray-800">{r.course_title}</td>
                      <td className="px-4 py-2 text-right">{r.learners}</td>
                      <td className="px-4 py-2 text-right">{Math.round(r.avg_percent * 100)}%</td>
                      <td className="px-4 py-2 text-right">{r.media_completions}</td>
                    </tr>
                  ))}
                  {analytics.length === 0 && <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">No consumption recorded yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="py-20 text-center"><Loader2 className="w-8 h-8 animate-spin mx-auto text-primary" /></div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Tracks list */}
          <div className="lg:col-span-1 space-y-2">
            <div className="flex items-center justify-between px-1">
              <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Tracks</h3>
              <button
                onClick={() => { setEditingTrackId(null); setTrackForm({ name: '', description: '', persona: '' }); }}
                className="text-primary hover:text-primary/80" title="New track"
              ><Plus className="w-4 h-4" /></button>
            </div>
            {tracks.map((t) => (
              <div
                key={t.id}
                className={`px-3 py-2.5 rounded-lg cursor-pointer flex items-center justify-between group ${activeTrackId === t.id ? 'bg-primary text-white' : 'hover:bg-gray-100 text-gray-700'}`}
                onClick={() => setActiveTrackId(t.id)}
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold truncate">{t.name}</div>
                  <div className={`text-[10px] ${activeTrackId === t.id ? 'text-white/70' : 'text-gray-400'}`}>
                    {t.course_count} courses · {t.status}{t.source === 'catalog' ? ' · catalog' : ''}
                  </div>
                </div>
                <div className={`flex items-center gap-1 ${activeTrackId === t.id ? '' : 'opacity-0 group-hover:opacity-100'}`}>
                  <button onClick={(e) => { e.stopPropagation(); setEditingTrackId(t.id); setTrackForm({ name: t.name, description: t.description || '', persona: t.persona || '' }); }}><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={(e) => { e.stopPropagation(); removeTrack(t.id); }}><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>
            ))}
            {tracks.length === 0 && <p className="text-sm text-gray-400 px-1">No tracks yet. Create one.</p>}
          </div>

          {/* Detail */}
          <div className="lg:col-span-3 space-y-4">
            {trackForm && (
              <Card><CardContent className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="font-semibold text-gray-800">{editingTrackId ? 'Edit Track' : 'New Track'}</h4>
                  <button onClick={() => { setTrackForm(null); setEditingTrackId(null); }}><X className="w-4 h-4 text-gray-400" /></button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div><label className={labelClass}>Name</label><input className={inputClass} value={trackForm.name} onChange={(e) => setTrackForm({ ...trackForm, name: e.target.value })} /></div>
                  <div><label className={labelClass}>Persona / Audience</label><input className={inputClass} value={trackForm.persona} onChange={(e) => setTrackForm({ ...trackForm, persona: e.target.value })} /></div>
                </div>
                <div><label className={labelClass}>Description</label><input className={inputClass} value={trackForm.description} onChange={(e) => setTrackForm({ ...trackForm, description: e.target.value })} /></div>
                <Button size="sm" onClick={saveTrack} disabled={busy}><Save className="w-4 h-4 mr-2" />Save Track</Button>
              </CardContent></Card>
            )}

            {activeTrack ? (
              <Card><CardContent className="p-0">
                <div className="px-4 py-3 border-b flex items-center justify-between">
                  <div>
                    <h3 className="font-bold text-gray-900">{activeTrack.name}</h3>
                    <p className="text-xs text-gray-500">{activeTrack.description || 'No description'}</p>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => { setEditingCourseId(null); setCourseForm({ ...emptyCourseForm }); }}>
                    <Plus className="w-4 h-4 mr-2" />Course
                  </Button>
                </div>

                {courseForm && (
                  <div className="p-4 bg-gray-50 border-b space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="font-semibold text-gray-800 text-sm">{editingCourseId ? 'Edit Course' : 'New Course'}</h4>
                      <button onClick={() => { setCourseForm(null); setEditingCourseId(null); }}><X className="w-4 h-4 text-gray-400" /></button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div><label className={labelClass}>Title</label><input className={inputClass} value={courseForm.title} onChange={(e) => setCourseForm({ ...courseForm, title: e.target.value })} /></div>
                      <div><label className={labelClass}>Course Code (for gates)</label><input className={inputClass} value={courseForm.course_code} onChange={(e) => setCourseForm({ ...courseForm, course_code: e.target.value })} /></div>
                      <div><label className={labelClass}>Section</label><input className={inputClass} value={courseForm.section} onChange={(e) => setCourseForm({ ...courseForm, section: e.target.value })} /></div>
                      <div><label className={labelClass}>Type</label><input className={inputClass} placeholder="eLearning / SelfPaced / Certification" value={courseForm.course_type} onChange={(e) => setCourseForm({ ...courseForm, course_type: e.target.value })} /></div>
                      <div><label className={labelClass}>Duration</label><input className={inputClass} placeholder="3 hrs" value={courseForm.duration} onChange={(e) => setCourseForm({ ...courseForm, duration: e.target.value })} /></div>
                      <div><label className={labelClass}>Catalog Deeplink URL</label><input className={inputClass} value={courseForm.external_url} onChange={(e) => setCourseForm({ ...courseForm, external_url: e.target.value })} /></div>
                    </div>
                    <div><label className={labelClass}>Description</label><input className={inputClass} value={courseForm.description} onChange={(e) => setCourseForm({ ...courseForm, description: e.target.value })} /></div>
                    <div><label className={labelClass}>Unlocks (display)</label><input className={inputClass} value={courseForm.unlocks} onChange={(e) => setCourseForm({ ...courseForm, unlocks: e.target.value })} /></div>
                    <Button size="sm" onClick={saveCourse} disabled={busy}><Save className="w-4 h-4 mr-2" />Save Course</Button>
                  </div>
                )}

                <div>
                  {(activeTrack.courses || []).map((c) => {
                    const open = expandedCourse.has(c.id);
                    return (
                      <div key={c.id} className="border-b last:border-0 border-gray-100">
                        <div className="flex items-center px-4 py-3 hover:bg-gray-50">
                          <button onClick={() => toggleCourse(c.id)} className="mr-2 text-gray-400">
                            {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                          </button>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-gray-900 truncate">{c.title}</span>
                              {c.external_url && <Link2 className="w-3.5 h-3.5 text-primary" />}
                              {c.source === 'catalog' && <span className="text-[9px] uppercase font-bold text-gray-400 border border-gray-200 rounded px-1">catalog</span>}
                            </div>
                            <div className="text-[11px] text-gray-400">
                              {c.course_code || 'no code'}{c.media?.length ? ` · ${c.media.length} media` : ''}{c.status !== 'published' ? ` · ${c.status}` : ''}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 text-gray-400">
                            <button onClick={() => editCourse(c)}><Pencil className="w-3.5 h-3.5" /></button>
                            <button onClick={() => removeCourse(c.id)}><Trash2 className="w-3.5 h-3.5" /></button>
                          </div>
                        </div>

                        {open && (
                          <div className="px-6 pb-4 space-y-2 bg-gray-50/40">
                            {(c.media || []).map((m) => (
                              <div key={m.id} className="flex items-center gap-2 text-sm py-1.5">
                                {m.kind === 'video' ? <Film className="w-4 h-4 text-primary" /> : <FileText className="w-4 h-4 text-secondary" />}
                                <span className="flex-1 text-gray-700 truncate">{m.title}</span>
                                <span className="text-[10px] text-gray-400">{m.kind}{m.size_bytes ? ` · ${(m.size_bytes / 1048576).toFixed(1)} MB` : ''}</span>
                                <button onClick={() => removeMedia(m.id)} className="text-gray-400 hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                              </div>
                            ))}
                            <div className="flex flex-wrap gap-2 pt-2">
                              <MediaUploadButton label="Upload Video" kind="video" accept="video/*" disabled={busy} onPick={(f) => uploadMedia(c.id, f, 'video')} />
                              <MediaUploadButton label="Upload Doc" kind="doc" accept=".pdf,.ppt,.pptx,.doc,.docx" disabled={busy} onPick={(f) => uploadMedia(c.id, f, f.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'doc')} />
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {(activeTrack.courses?.length || 0) === 0 && <p className="px-4 py-8 text-center text-sm text-gray-400">No courses yet. Add one.</p>}
                </div>
              </CardContent></Card>
            ) : (
              <Card><CardContent className="p-10 text-center text-gray-400">Select or create a track to begin.</CardContent></Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function MediaUploadButton({ label, accept, disabled, onPick }: { label: string; kind: string; accept: string; disabled?: boolean; onPick: (f: File) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => ref.current?.click()}>
        <Upload className="w-3.5 h-3.5 mr-2" />{label}
      </Button>
      <input ref={ref} type="file" accept={accept} className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); if (ref.current) ref.current.value = ''; }} />
    </>
  );
}
