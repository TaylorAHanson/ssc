/**
 * Markdown → sanitized HTML conversion for chat content.
 *
 * Used to render Databricks Genie's ``final_answer`` (GFM markdown —
 * headings, bullets, inline tables) and other agent/tool markdown
 * directly in the bubble without round-tripping through the LLM.
 *
 * Although the markdown originates server-side under the user's own
 * OBO identity, individual table cells can contain arbitrary *data*
 * values (a row whose text happens to be ``<img src=x onerror=...>``),
 * so the rendered HTML is treated as untrusted and sanitized with a
 * strict allowlist before it ever reaches ``dangerouslySetInnerHTML``.
 *
 * Sanitization uses the browser's own HTML parser (``DOMParser``) and
 * an element/attribute allowlist rather than regexes — regex-based HTML
 * sanitizers are notoriously bypassable (malformed tags, nested
 * comments, attribute splitting). A regex fallback is kept only for
 * non-DOM environments (it never runs in the browser app).
 */
import { marked } from 'marked';

marked.setOptions({
    gfm: true,
    breaks: false,
});

// Elements marked emits for GFM, plus the inline formatting we want to keep.
const ALLOWED_TAGS = new Set([
    'a', 'p', 'br', 'hr', 'span', 'div',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins', 'mark', 'small', 'sub', 'sup',
    'code', 'pre', 'kbd', 'samp', 'var', 'blockquote',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'img',
]);

// Tags whose entire subtree must be dropped (active content / never benign).
const DANGEROUS_TAGS = new Set([
    'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'link',
    'meta', 'base', 'form', 'input', 'button', 'textarea', 'select', 'option',
    'noscript', 'template', 'frame', 'frameset', 'applet', 'audio', 'video',
    'source', 'track', 'canvas', 'portal',
]);

// Attributes allowed per tag (plus the global set). Everything else (and any
// ``on*`` handler) is stripped.
const GLOBAL_ATTRS = new Set(['title', 'align', 'colspan', 'rowspan']);
const ATTRS_BY_TAG: Record<string, Set<string>> = {
    a: new Set(['href', 'title', 'target', 'rel']),
    img: new Set(['src', 'alt', 'title', 'width', 'height']),
    td: new Set(['align', 'colspan', 'rowspan']),
    th: new Set(['align', 'colspan', 'rowspan', 'scope']),
    col: new Set(['span']),
    ol: new Set(['start', 'type']),
};

function _isAbsolute(href: string): boolean {
    return (
        /^https?:\/\//i.test(href) ||
        href.startsWith('//') ||
        /^mailto:/i.test(href) ||
        /^tel:/i.test(href)
    );
}

/** True for a URL whose scheme is safe to keep in href/src. */
function _safeUrl(value: string, allowDataImage: boolean): boolean {
    const v = value.trim();
    if (v === '') return false;
    // Relative URLs, anchors, and query/path-only links are fine.
    if (/^[#/?]/.test(v)) return true;
    if (/^https?:\/\//i.test(v) || v.startsWith('//')) return true;
    if (/^(mailto|tel):/i.test(v)) return true;
    if (allowDataImage && /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(v)) {
        // Disallow svg data URIs (can carry scripts) even here.
        return !/^data:image\/svg/i.test(v);
    }
    // Anything else (javascript:, data:text/html, vbscript:, unknown scheme) is rejected.
    return false;
}

function _scrubElement(el: Element): void {
    const tag = el.tagName.toLowerCase();
    const allowed = ATTRS_BY_TAG[tag] ?? new Set<string>();
    for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on')) {
            el.removeAttribute(attr.name);
            continue;
        }
        if (!allowed.has(name) && !GLOBAL_ATTRS.has(name)) {
            el.removeAttribute(attr.name);
            continue;
        }
        if (name === 'href' || name === 'src') {
            if (!_safeUrl(attr.value, /* allowDataImage */ tag === 'img')) {
                el.removeAttribute(attr.name);
            }
        }
    }
    // External links open in a new tab so following one doesn't unmount the SPA
    // (which would lose the user's chat history mid-conversation).
    if (tag === 'a') {
        const href = el.getAttribute('href') ?? '';
        if (_isAbsolute(href)) {
            el.setAttribute('target', '_blank');
            el.setAttribute('rel', 'noopener noreferrer');
        }
    }
}

function _sanitizeNode(node: Node): void {
    // Snapshot children first — we mutate the tree as we go.
    for (const child of Array.from(node.childNodes)) {
        if (child.nodeType !== 1 /* ELEMENT_NODE */) continue;
        const el = child as Element;
        const tag = el.tagName.toLowerCase();
        if (DANGEROUS_TAGS.has(tag)) {
            el.remove();
            continue;
        }
        if (!ALLOWED_TAGS.has(tag)) {
            // Unknown-but-not-dangerous: unwrap so we keep the text content but
            // drop the tag (and its attributes/handlers).
            _sanitizeNode(el);
            while (el.firstChild) el.parentNode?.insertBefore(el.firstChild, el);
            el.remove();
            continue;
        }
        _scrubElement(el);
        _sanitizeNode(el);
    }
}

// Regex fallback (non-browser only). Best-effort; the DOM path is authoritative.
const _SCRIPT_RE = /<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi;
const _ON_HANDLER_RE = /\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi;
const _JS_HREF_RE = /\s(href|src)\s*=\s*("(javascript|data|vbscript):[^"]*"|'(javascript|data|vbscript):[^']*')/gi;

/**
 * Convert agent/Genie markdown to HTML safe for ``dangerouslySetInnerHTML``.
 */
export function renderMarkdownSafe(markdown: string): string {
    if (!markdown) return '';
    const html = marked.parse(markdown, { async: false }) as string;

    if (typeof DOMParser === 'undefined') {
        // Non-DOM environment (SSR / some test runners): best-effort regex strip.
        return html
            .replace(_SCRIPT_RE, '')
            .replace(_ON_HANDLER_RE, '')
            .replace(_JS_HREF_RE, ' $1=""');
    }

    const doc = new DOMParser().parseFromString(html, 'text/html');
    _sanitizeNode(doc.body);
    return doc.body.innerHTML;
}
