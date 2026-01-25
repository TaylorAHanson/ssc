import { useState, useMemo } from 'react';
import { useAssetStore } from '../stores/assetStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import {
  Search,
  Plus,
  X,
  Github,
  ExternalLink,
  Tag,
  User,
  Users,
  Eye,
  FileText,
  Video,
  Link as LinkIcon,
  Calendar,
  Send
} from 'lucide-react';
import { format } from 'date-fns';
import type { DesignPattern, AssetLink } from '../types';

const ASSET_LINK_ICONS = {
  github: Github,
  confluence: FileText,
  video: Video,
  documentation: FileText,
  other: LinkIcon,
};

const COMMON_TAGS = [
  'pipeline',
  'dashboard',
  'etl',
  'ml',
  'mlops',
  'databricks',
  'spark',
  'react',
  'visualization',
  'monitoring',
  'data-quality',
  'd3',
];

export function ReusableAssets() {
  const designPatterns = useAssetStore((state) => state.designPatterns);
  const addDesignPattern = useAssetStore((state) => state.addDesignPattern);
  const addComment = useAssetStore((state) => state.addComment);
  const incrementViewCount = useAssetStore((state) => state.incrementViewCount);

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [showSubmissionForm, setShowSubmissionForm] = useState(false);
  const [selectedPattern, setSelectedPattern] = useState<DesignPattern | null>(null);
  const [newComment, setNewComment] = useState('');

  // Submission form state
  const [submissionForm, setSubmissionForm] = useState({
    title: '',
    description: '',
    author: '',
    authorEmail: '',
    team: '',
    tags: [] as string[],
    githubUrl: '',
    assetLinks: [] as Omit<AssetLink, 'id'>[],
  });

  const filteredPatterns = useMemo(() => {
    return designPatterns.filter((pattern) => {
      const matchesSearch =
        pattern.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        pattern.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        pattern.team.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesTags =
        selectedTags.length === 0 ||
        selectedTags.every((tag) => pattern.tags.includes(tag));

      return matchesSearch && matchesTags;
    });
  }, [designPatterns, searchQuery, selectedTags]);

  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    designPatterns.forEach((pattern) => {
      pattern.tags.forEach((tag) => tagSet.add(tag));
    });
    return Array.from(tagSet).sort();
  }, [designPatterns]);

  const handleTagToggle = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const handleViewPattern = (pattern: DesignPattern) => {
    setSelectedPattern(pattern);
    incrementViewCount(pattern.id);
  };

  const handleAddAssetLink = () => {
    setSubmissionForm((prev) => ({
      ...prev,
      assetLinks: [
        ...prev.assetLinks,
        { type: 'other', label: '', url: '' },
      ],
    }));
  };

  const handleRemoveAssetLink = (index: number) => {
    setSubmissionForm((prev) => ({
      ...prev,
      assetLinks: prev.assetLinks.filter((_, i) => i !== index),
    }));
  };

  const handleAddTag = (tag: string) => {
    if (!submissionForm.tags.includes(tag)) {
      setSubmissionForm((prev) => ({
        ...prev,
        tags: [...prev.tags, tag],
      }));
    }
  };

  const handleRemoveTag = (tag: string) => {
    setSubmissionForm((prev) => ({
      ...prev,
      tags: prev.tags.filter((t) => t !== tag),
    }));
  };

  const handleSubmitPattern = async () => {
    if (
      !submissionForm.title ||
      !submissionForm.description ||
      !submissionForm.githubUrl ||
      !submissionForm.author ||
      !submissionForm.authorEmail ||
      !submissionForm.team
    ) {
      alert('Please fill in all required fields');
      return;
    }

    await addDesignPattern({
      title: submissionForm.title,
      description: submissionForm.description,
      author: submissionForm.author,
      authorEmail: submissionForm.authorEmail,
      team: submissionForm.team,
      tags: submissionForm.tags,
      githubUrl: submissionForm.githubUrl,
      assetLinks: submissionForm.assetLinks.map((link, index) => ({
        id: `link-${Date.now()}-${index}`,
        ...link,
      })),
    });

    // Reset form
    setSubmissionForm({
      title: '',
      description: '',
      author: '',
      authorEmail: '',
      team: '',
      tags: [],
      githubUrl: '',
      assetLinks: [],
    });
    setShowSubmissionForm(false);
  };

  const handleSubmitComment = async () => {
    if (!selectedPattern || !newComment.trim()) return;

    await addComment(selectedPattern.id, {
      designPatternId: selectedPattern.id,
      author: 'Current User', // In real app, get from auth context
      authorEmail: 'user@example.com',
      content: newComment,
    });

    setNewComment('');
    // Refresh selected pattern
    const updatedPattern = designPatterns.find((p) => p.id === selectedPattern.id);
    if (updatedPattern) {
      setSelectedPattern(updatedPattern);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Reusable Assets</h1>
          <p className="text-gray-600">
            Discover and share design patterns, templates, and reusable components
          </p>
        </div>
        <Button onClick={() => setShowSubmissionForm(true)} className="flex items-center gap-2">
          <Plus className="w-4 h-4" />
          Submit Pattern
        </Button>
      </div>

      {/* Search and Filters */}
      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5" />
            <Input
              type="text"
              placeholder="Search patterns by title, description, or team..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>

          <div className="space-y-2">
            <div className="text-sm font-semibold text-gray-700">Filter by Tags:</div>
            <div className="flex flex-wrap gap-2">
              {allTags.map((tag) => (
                <button
                  key={tag}
                  onClick={() => handleTagToggle(tag)}
                  className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${selectedTags.includes(tag)
                    ? 'bg-primary text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    }`}
                >
                  <Tag className="w-3 h-3 inline mr-1" />
                  {tag}
                </button>
              ))}
            </div>
            {selectedTags.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedTags([])}
                className="mt-2"
              >
                Clear Filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Results Count */}
      <div className="text-sm text-gray-600">
        Showing {filteredPatterns.length} of {designPatterns.length} design patterns
      </div>

      {/* Design Patterns Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredPatterns.map((pattern) => (
          <Card
            key={pattern.id}
            className="hover:shadow-lg transition-shadow cursor-pointer"
            onClick={() => handleViewPattern(pattern)}
          >
            <CardHeader>
              <div className="flex items-start justify-between">
                <CardTitle className="text-lg">{pattern.title}</CardTitle>
                <Github className="w-5 h-5 text-gray-400 flex-shrink-0" />
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-gray-600 line-clamp-3">{pattern.description}</p>

              <div className="flex flex-wrap gap-2">
                {pattern.tags.slice(0, 3).map((tag) => (
                  <span
                    key={tag}
                    className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs font-medium"
                  >
                    {tag}
                  </span>
                ))}
                {pattern.tags.length > 3 && (
                  <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs font-medium">
                    +{pattern.tags.length - 3}
                  </span>
                )}
              </div>

              <div className="flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-200">
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-1">
                    <User className="w-3 h-3" />
                    <span>{pattern.author}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Users className="w-3 h-3" />
                    <span>{pattern.team}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Eye className="w-3 h-3" />
                  <span>{pattern.viewCount}</span>
                </div>
              </div>

              <div className="flex items-center gap-2 text-xs text-gray-500">
                <Calendar className="w-3 h-3" />
                <span>{format(new Date(pattern.createdAt), 'MMM d, yyyy')}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {filteredPatterns.length === 0 && (
        <Card>
          <CardContent className="p-12 text-center">
            <p className="text-gray-600">No design patterns found matching your criteria.</p>
          </CardContent>
        </Card>
      )}

      {/* Submission Form Modal */}
      {showSubmissionForm && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col bg-white">
            <CardHeader className="flex-shrink-0 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <CardTitle>Submit Design Pattern</CardTitle>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowSubmissionForm(false)}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto space-y-4 p-6">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Title *
                </label>
                <Input
                  value={submissionForm.title}
                  onChange={(e) =>
                    setSubmissionForm((prev) => ({ ...prev, title: e.target.value }))
                  }
                  placeholder="e.g., ETL Pipeline Template"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Description *
                </label>
                <textarea
                  value={submissionForm.description}
                  onChange={(e) =>
                    setSubmissionForm((prev) => ({ ...prev, description: e.target.value }))
                  }
                  placeholder="Provide a short description of the design pattern..."
                  className="w-full min-h-[100px] rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Author *
                  </label>
                  <Input
                    value={submissionForm.author}
                    onChange={(e) =>
                      setSubmissionForm((prev) => ({ ...prev, author: e.target.value }))
                    }
                    placeholder="Your name"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Email *
                  </label>
                  <Input
                    type="email"
                    value={submissionForm.authorEmail}
                    onChange={(e) =>
                      setSubmissionForm((prev) => ({ ...prev, authorEmail: e.target.value }))
                    }
                    placeholder="your.email@example.com"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Team *
                </label>
                <Input
                  value={submissionForm.team}
                  onChange={(e) =>
                    setSubmissionForm((prev) => ({ ...prev, team: e.target.value }))
                  }
                  placeholder="e.g., Data Engineering"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  GitHub Repository URL *
                </label>
                <Input
                  type="url"
                  value={submissionForm.githubUrl}
                  onChange={(e) =>
                    setSubmissionForm((prev) => ({ ...prev, githubUrl: e.target.value }))
                  }
                  placeholder="https://github.com/org/repo"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Tags
                </label>
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-2">
                    {COMMON_TAGS.map((tag) => (
                      <button
                        key={tag}
                        onClick={() => handleAddTag(tag)}
                        disabled={submissionForm.tags.includes(tag)}
                        className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${submissionForm.tags.includes(tag)
                          ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                          }`}
                      >
                        <Tag className="w-3 h-3 inline mr-1" />
                        {tag}
                      </button>
                    ))}
                  </div>
                  {submissionForm.tags.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {submissionForm.tags.map((tag) => (
                        <span
                          key={tag}
                          className="px-3 py-1 bg-primary text-white rounded-full text-sm font-medium flex items-center gap-2"
                        >
                          {tag}
                          <button
                            onClick={() => handleRemoveTag(tag)}
                            className="hover:bg-primary/80 rounded-full p-0.5"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-gray-700">
                    Additional Asset Links
                  </label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleAddAssetLink}
                  >
                    <Plus className="w-4 h-4 mr-1" />
                    Add Link
                  </Button>
                </div>
                <div className="space-y-2">
                  {submissionForm.assetLinks.map((link, index) => {
                    return (
                      <div key={index} className="flex gap-2 items-start">
                        <select
                          value={link.type}
                          onChange={(e) => {
                            const newLinks = [...submissionForm.assetLinks];
                            newLinks[index].type = e.target.value as AssetLink['type'];
                            setSubmissionForm((prev) => ({ ...prev, assetLinks: newLinks }));
                          }}
                          className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
                        >
                          <option value="confluence">Confluence</option>
                          <option value="video">Video</option>
                          <option value="documentation">Documentation</option>
                          <option value="other">Other</option>
                        </select>
                        <Input
                          placeholder="Label"
                          value={link.label}
                          onChange={(e) => {
                            const newLinks = [...submissionForm.assetLinks];
                            newLinks[index].label = e.target.value;
                            setSubmissionForm((prev) => ({ ...prev, assetLinks: newLinks }));
                          }}
                          className="flex-1"
                        />
                        <Input
                          type="url"
                          placeholder="URL"
                          value={link.url}
                          onChange={(e) => {
                            const newLinks = [...submissionForm.assetLinks];
                            newLinks[index].url = e.target.value;
                            setSubmissionForm((prev) => ({ ...prev, assetLinks: newLinks }));
                          }}
                          className="flex-1"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => handleRemoveAssetLink(index)}
                        >
                          <X className="w-4 h-4" />
                        </Button>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex gap-3 pt-4">
                <Button onClick={handleSubmitPattern} className="flex-1">
                  Submit Pattern
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setShowSubmissionForm(false)}
                >
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Pattern Detail Modal */}
      {selectedPattern && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col bg-white">
            <CardHeader className="flex-shrink-0 border-b border-gray-200">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <CardTitle className="text-2xl mb-2">{selectedPattern.title}</CardTitle>
                  <div className="flex items-center gap-4 text-sm text-gray-600">
                    <div className="flex items-center gap-1">
                      <User className="w-4 h-4" />
                      <span>{selectedPattern.author}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Users className="w-4 h-4" />
                      <span>{selectedPattern.team}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Calendar className="w-4 h-4" />
                      <span>{format(new Date(selectedPattern.createdAt), 'MMM d, yyyy')}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Eye className="w-4 h-4" />
                      <span>{selectedPattern.viewCount} views</span>
                    </div>
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedPattern(null)}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto space-y-6 p-6">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Description</h3>
                <p className="text-gray-700">{selectedPattern.description}</p>
              </div>

              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Tags</h3>
                <div className="flex flex-wrap gap-2">
                  {selectedPattern.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-3 py-1 bg-gray-100 text-gray-700 rounded-full text-sm font-medium"
                    >
                      <Tag className="w-3 h-3 inline mr-1" />
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">GitHub Repository</h3>
                <a
                  href={selectedPattern.githubUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 text-primary hover:underline"
                >
                  <Github className="w-5 h-5" />
                  <span>{selectedPattern.githubUrl}</span>
                  <ExternalLink className="w-4 h-4" />
                </a>
              </div>

              {selectedPattern.assetLinks.length > 0 && (
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">Additional Resources</h3>
                  <div className="space-y-2">
                    {selectedPattern.assetLinks.map((link) => {
                      const Icon = ASSET_LINK_ICONS[link.type] || LinkIcon;
                      return (
                        <a
                          key={link.id}
                          href={link.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-2 p-3 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors"
                        >
                          <Icon className="w-5 h-5 text-gray-600" />
                          <span className="text-gray-900">{link.label}</span>
                          <ExternalLink className="w-4 h-4 text-gray-400 ml-auto" />
                        </a>
                      );
                    })}
                  </div>
                </div>
              )}

              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-4">
                  Comments ({selectedPattern.comments.length})
                </h3>
                <div className="space-y-4 mb-4">
                  {selectedPattern.comments.length === 0 ? (
                    <p className="text-gray-500 text-sm">No comments yet. Be the first to suggest an update!</p>
                  ) : (
                    selectedPattern.comments.map((comment) => (
                      <div key={comment.id} className="border-l-4 border-primary pl-4 py-2">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-gray-900">{comment.author}</span>
                          <span className="text-xs text-gray-500">
                            {format(new Date(comment.createdAt), 'MMM d, yyyy')}
                          </span>
                        </div>
                        <p className="text-gray-700">{comment.content}</p>
                      </div>
                    ))
                  )}
                </div>
                <div className="flex gap-2">
                  <Input
                    placeholder="Add a comment or suggestion..."
                    value={newComment}
                    onChange={(e) => setNewComment(e.target.value)}
                    onKeyPress={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmitComment();
                      }
                    }}
                  />
                  <Button onClick={handleSubmitComment} disabled={!newComment.trim()}>
                    <Send className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

