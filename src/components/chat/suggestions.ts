/**
 * Next-step suggestions: the clickable replies under the latest agent message.
 *
 * The backend parses the model's ```suggestions block and sends the list as
 * `MessageEvent.suggestions` with the block already removed from `content`
 * (see backend/app/agents/suggestions.py). This module is the client-side
 * safety net: it strips any block that still reaches the browser, complete or
 * cut off, so raw JSON never shows in a bubble, and it re-checks the structured
 * list since a click sends the text verbatim as the user's message.
 */

export const MAX_SUGGESTIONS = 4;
export const MAX_SUGGESTION_CHARS = 80;

const COMPLETE_BLOCK = /```[ \t]*suggestions\b[ \t]*\r?\n?([\s\S]*?)```[ \t]*/gi;
const OPEN_BLOCK = /```[ \t]*suggestions\b[\s\S]*$/i;
// The fence opener itself arriving in pieces ("```sugg"). A bare "```" is left
// alone: it could be the closing fence of a real code block.
const PARTIAL_OPENER = /```[ \t]*s(?:u(?:g(?:g(?:e(?:s(?:t(?:i(?:o(?:n(?:s)?)?)?)?)?)?)?)?)?)?[ \t]*$/i;

export function sanitizeSuggestions(raw: unknown): string[] {
    if (!Array.isArray(raw)) return [];
    const out: string[] = [];
    const seen = new Set<string>();
    for (const item of raw) {
        if (typeof item !== 'string') continue;
        const text = item.replace(/\s+/g, ' ').trim();
        if (!text || text.length > MAX_SUGGESTION_CHARS) continue;
        const key = text.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(text);
        if (out.length >= MAX_SUGGESTIONS) break;
    }
    return out;
}

function parseBody(body: string): string[] {
    const text = body.trim();
    const start = text.indexOf('[');
    const end = text.lastIndexOf(']');
    if (start === -1 || end <= start) return [];
    try {
        return sanitizeSuggestions(JSON.parse(text.slice(start, end + 1)));
    } catch {
        return [];
    }
}

/** Content with any suggestions block removed, plus what the last complete block held. */
export function splitSuggestions(content: string): { content: string; suggestions: string[] } {
    const blocks = [...content.matchAll(COMPLETE_BLOCK)];
    const cleaned = content
        .replace(COMPLETE_BLOCK, '')
        .replace(OPEN_BLOCK, '')
        .replace(PARTIAL_OPENER, '');
    if (cleaned === content) return { content, suggestions: [] };
    const last = blocks[blocks.length - 1];
    return {
        content: cleaned.trim(),
        suggestions: last ? parseBody(last[1] ?? '') : [],
    };
}
