import { cn } from '../../lib/utils';

interface PromptChipProps {
    text: string;
    onSelect: (text: string) => void;
    disabled?: boolean;
    className?: string;
}

/** A clickable prompt that sends its text as the user's message. */
export function PromptChip({ text, onSelect, disabled, className }: PromptChipProps) {
    return (
        <button
            type="button"
            onClick={() => onSelect(text)}
            disabled={disabled}
            className={cn(
                'relative p-2.5 rounded-xl border border-gray-200 hover:shadow-md hover:bg-white/80 hover:border-primary/50 transition-all duration-200 text-left group disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:border-primary/50',
                className,
            )}
        >
            <span className="block text-[13px] font-medium text-gray-900 group-hover:text-primary transition-colors">
                {text}
            </span>
        </button>
    );
}

interface NextStepSuggestionsProps {
    suggestions: string[];
    onSelect: (text: string) => void;
}

/** The suggested replies offered under the agent's latest message. */
export function NextStepSuggestions({ suggestions, onSelect }: NextStepSuggestionsProps) {
    if (suggestions.length === 0) return null;
    return (
        <div
            role="group"
            aria-label="Suggested replies"
            className="flex flex-wrap gap-2 max-w-[80%] animate-in fade-in slide-in-from-bottom-1 duration-300"
        >
            {suggestions.map((text) => (
                <PromptChip
                    key={text}
                    text={text}
                    onSelect={onSelect}
                    className="px-3 py-1.5 bg-white/60 max-w-full"
                />
            ))}
        </div>
    );
}
