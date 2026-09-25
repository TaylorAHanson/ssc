/**
 * Chat transcript export: the visible conversation, including tool calls with
 * their arguments and results, as a Markdown file for sharing an experience
 * (a bug report, a demo, a review of how the agent behaved).
 */
import type { DisplayMessage } from './chatTypes';

// Tool results can be whole tables; keep the file readable and shareable.
const MAX_TOOL_JSON_CHARS = 4000;

function fence(value: unknown): string {
    let text: string;
    try {
        text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    } catch {
        text = String(value);
    }
    if (text.length > MAX_TOOL_JSON_CHARS) {
        text = `${text.slice(0, MAX_TOOL_JSON_CHARS)}\n… (${text.length - MAX_TOOL_JSON_CHARS} more characters)`;
    }
    // A longer fence than any backtick run inside keeps the block intact.
    const longest = Math.max(2, ...(text.match(/`+/g) ?? []).map((run) => run.length));
    const ticks = '`'.repeat(longest + 1);
    return `${ticks}\n${text}\n${ticks}`;
}

function toolSection(msg: Extract<DisplayMessage, { kind: 'tool' }>): string {
    const secs = msg.completedAt ? ` · ${((msg.completedAt - msg.startedAt) / 1000).toFixed(1)}s` : '';
    const lines = [`**Tool: \`${msg.toolName}\`** (${msg.status}${secs})`];
    if (msg.toolArguments && Object.keys(msg.toolArguments).length > 0) {
        lines.push('Arguments:', fence(msg.toolArguments));
    }
    if (msg.errorMessage) lines.push(`Error: ${msg.errorMessage}`);
    const result = msg.genieResult ?? msg.toolResult;
    if (result !== undefined) lines.push('Result:', fence(result));
    return lines.join('\n\n');
}

export function buildTranscriptMarkdown(messages: DisplayMessage[], title: string): string {
    const header = [
        `# ${title}`,
        '',
        `- Exported: ${new Date().toISOString()}`,
        `- Page: ${window.location.pathname}`,
        '',
    ].join('\n');
    const body = messages.map((msg) => {
        switch (msg.kind) {
            case 'user':
                return `## User · ${msg.timestamp}\n\n${msg.content}`;
            case 'agent':
                return `## Agent · ${msg.timestamp}\n\n${msg.content}`;
            case 'reasoning':
                return `> _Reasoning:_ ${msg.text.replace(/\n/g, '\n> ')}`;
            case 'tool':
                return toolSection(msg);
        }
    });
    return `${header}\n${body.join('\n\n---\n\n')}\n`;
}

export function downloadTranscript(messages: DisplayMessage[], title = 'Chat transcript'): void {
    const markdown = buildTranscriptMarkdown(messages, title);
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const link = document.createElement('a');
    link.href = url;
    link.download = `chat-transcript-${stamp}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoked after the click is handled; revoking synchronously can cancel it.
    setTimeout(() => URL.revokeObjectURL(url), 0);
}
