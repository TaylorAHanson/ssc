/**
 * Markdown → sanitized HTML conversion for chat content.
 *
 * Used today to render Databricks Genie's ``final_answer`` field
 * (which ships as GFM markdown — headings, bullets, inline tables)
 * directly in the agent bubble without round-tripping it through the
 * LLM for rephrasing. The LLM previously summarized Genie's output,
 * which added latency and occasionally distorted the answer; passing
 * the markdown through verbatim is faster and higher fidelity.
 *
 * We use ``marked`` for the conversion (small, fast, GFM-aware) and
 * trust Genie as the source — it runs server-side under the user's
 * own OBO identity and isn't a place where third-party scripts could
 * be injected. We still strip ``<script>`` and event-handler
 * attributes defensively in case a row of user data contained one.
 */
import { marked } from 'marked';

marked.setOptions({
    gfm: true,
    breaks: false,
});

const _SCRIPT_RE = /<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi;
const _ON_HANDLER_RE = /\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi;
const _JS_HREF_RE = /\s(href|src)\s*=\s*("javascript:[^"]*"|'javascript:[^']*')/gi;

// Anchor tag detection. We add ``target="_blank" rel="noopener
// noreferrer"`` to any link whose ``href`` is absolute (http/https,
// protocol-relative, mailto, tel) so external destinations don't
// hijack the SPA. Relative links (``/requests/...``, ``#anchor``) are
// left alone so React Router handles them in-app.
const _ANCHOR_RE = /<a\b([^>]*?)\shref=("([^"]*)"|'([^']*)')([^>]*?)>/gi;

/**
 * Convert Genie-supplied markdown to HTML safe for use in
 * ``dangerouslySetInnerHTML``. The few sanitization rules below
 * cover the realistic risk surface (data cells containing HTML
 * fragments) without pulling in a full sanitizer library.
 *
 * Also ensures absolute links open in a new tab — agent responses
 * frequently link to external Databricks docs / dashboards / Genie
 * conversations, and following them in the same tab would unmount
 * the chat (losing the user's history mid-conversation).
 */
export function renderMarkdownSafe(markdown: string): string {
    if (!markdown) return '';
    const html = marked.parse(markdown, { async: false }) as string;
    return html
        .replace(_SCRIPT_RE, '')
        .replace(_ON_HANDLER_RE, '')
        .replace(_JS_HREF_RE, ' $1=""')
        .replace(_ANCHOR_RE, _rewriteAnchor);
}

function _rewriteAnchor(
    match: string,
    preAttrs: string,
    _quoted: string,
    doubleQuoted: string | undefined,
    singleQuoted: string | undefined,
    postAttrs: string,
): string {
    const href = (doubleQuoted ?? singleQuoted ?? '').trim();
    const isAbsolute =
        /^https?:\/\//i.test(href) ||
        href.startsWith('//') ||
        /^mailto:/i.test(href) ||
        /^tel:/i.test(href);
    if (!isAbsolute) return match;
    // Don't override an explicit target the markdown author already
    // chose (rare, but possible if they hand-wrote inline HTML).
    const allAttrs = `${preAttrs} ${postAttrs}`;
    if (/\btarget\s*=/i.test(allAttrs)) return match;
    const quote = doubleQuoted !== undefined ? '"' : "'";
    return (
        `<a${preAttrs} href=${quote}${href}${quote}` +
        ` target="_blank" rel="noopener noreferrer"${postAttrs}>`
    );
}
