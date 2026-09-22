import { useState, useRef, useEffect, useTransition } from 'react';
import { Search, X, Sparkles } from 'lucide-react';

interface DiscoverSearchProps {
  value: string;
  onChange: (value: string) => void;
  onSubmitAgentQuery?: (query: string) => void;
  placeholder?: string;
  className?: string;
  expandedWidth?: string;
}

export function DiscoverSearch({
  value,
  onChange,
  onSubmitAgentQuery,
  placeholder = 'Search metrics, domains, tables...',
  className = '',
  expandedWidth = 'w-56 sm:w-72 md:w-80',
}: DiscoverSearchProps) {
  const [isExpanded, setIsExpanded] = useState<boolean>(() => Boolean(value));
  const [localValue, setLocalValue] = useState<string>(value);
  const [prevPropValue, setPrevPropValue] = useState<string>(value);
  const [, startTransition] = useTransition();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Synchronize localValue during render when value prop changes externally
  if (value !== prevPropValue) {
    setPrevPropValue(value);
    setLocalValue(value);
    if (value) {
      setIsExpanded(true);
    }
  }

  // Click outside to collapse if empty
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        if (!localValue.trim()) {
          setIsExpanded(false);
        }
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [localValue]);

  // Handle global "/" shortcut to focus search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.key === '/' &&
        !['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)
      ) {
        e.preventDefault();
        setIsExpanded(true);
        setTimeout(() => inputRef.current?.focus(), 20);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleChange = (newVal: string) => {
    // Instant local state update for zero typing lag
    setLocalValue(newVal);
    // Low priority transition for parent/filtering update
    startTransition(() => {
      onChange(newVal);
    });
  };

  const handleClear = () => {
    setLocalValue('');
    onChange('');
    inputRef.current?.focus();
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (localValue.trim() && onSubmitAgentQuery) {
      onSubmitAgentQuery(localValue);
    }
  };

  if (!isExpanded && !value) {
    return (
      <button
        type="button"
        onClick={() => {
          setIsExpanded(true);
          setTimeout(() => inputRef.current?.focus(), 25);
        }}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200/90 text-slate-600 hover:text-slate-900 text-xs font-semibold transition-all cursor-pointer shadow-2xs group ${className}`}
        title="Search catalog or ask AI (Press /)"
      >
        <Search className="w-3.5 h-3.5 text-slate-500 group-hover:text-primary transition-colors" />
        <span className="hidden sm:inline">Search</span>
      </button>
    );
  }

  return (
    <div ref={containerRef} className={`relative flex items-center ${className}`}>
      <form onSubmit={handleSubmit} className="relative flex items-center">
        <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={localValue}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              if (localValue) {
                handleClear();
              } else {
                setIsExpanded(false);
              }
            }
          }}
          placeholder={placeholder}
          aria-label="Search catalog or ask AI"
          className={`pl-8 pr-16 py-1.5 bg-white border border-slate-300 rounded-xl text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all shadow-xs ${expandedWidth}`}
        />

        <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-1">
          {localValue ? (
            <button
              type="button"
              onClick={handleClear}
              className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors cursor-pointer"
              title="Clear search"
            >
              <X className="w-3 h-3" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setIsExpanded(false)}
              className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors cursor-pointer"
              title="Collapse search"
            >
              <X className="w-3 h-3" />
            </button>
          )}

          {onSubmitAgentQuery && (
            <button
              type="submit"
              disabled={!localValue.trim()}
              className="p-1 px-1.5 rounded-lg bg-primary hover:bg-primary/90 disabled:opacity-30 disabled:hover:bg-primary text-white transition-all cursor-pointer flex items-center gap-0.5 text-[11px] font-semibold"
              title="Ask AI (Enter)"
            >
              <Sparkles className="w-3 h-3" />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
