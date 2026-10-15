import { useMemo } from 'react';
import type { MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Marked, Renderer } from 'marked';
import { applyBrand, headingText, resolveLink, slugify } from './content';
import type { GuidePage } from './content';

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const ANCHOR_CLASS = 'ml-2 font-normal no-underline opacity-0 transition-opacity group-hover:opacity-60 focus:opacity-60';

const htmlCache = new Map<string, string>();

// Guide pages are bundled repo markdown, so they skip renderMarkdownSafe: its
// sanitizer drops the heading ids and relative links the guide relies on.
function pageHtml(page: GuidePage): string {
  const cached = htmlCache.get(page.file);
  if (cached !== undefined) return cached;
  const md = new Marked({ gfm: true, breaks: false });
  md.use({
    renderer: {
      heading({ tokens, depth, text }) {
        const id = slugify(headingText(text));
        const anchor =
          depth > 1 ? `<a href="#${id}" class="${ANCHOR_CLASS}" aria-label="Link to this section">#</a>` : '';
        return `<h${depth} id="${id}" class="group scroll-mt-6">${this.parser.parseInline(tokens)}${anchor}</h${depth}>\n`;
      },
      link({ href, title, tokens }) {
        const inner = this.parser.parseInline(tokens);
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
        const target = resolveLink(href, page.file);
        if (target.kind === 'external') {
          return `<a href="${escapeHtml(target.href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${inner}</a>`;
        }
        if (target.kind === 'internal') {
          return `<a href="${escapeHtml(target.to)}"${titleAttr}>${inner}</a>`;
        }
        return `<span title="${escapeHtml(target.path)} (in the source repository)">${inner}</span>`;
      },
      table(token) {
        return `<div class="overflow-x-auto">${Renderer.prototype.table.call(this, token)}</div>`;
      },
    },
  });
  const html = md.parse(page.source, { async: false });
  htmlCache.set(page.file, html);
  return html;
}

interface GuideMarkdownProps {
  page: GuidePage;
  brandName: string;
  brandShortName: string;
}

export function GuideMarkdown({ page, brandName, brandShortName }: GuideMarkdownProps) {
  const navigate = useNavigate();
  const html = useMemo(
    () => applyBrand(pageHtml(page), { name: escapeHtml(brandName), shortName: escapeHtml(brandShortName) }),
    [page, brandName, brandShortName],
  );

  const handleClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = (event.target as HTMLElement).closest('a');
    const href = link?.getAttribute('href');
    if (!link || !href || link.target === '_blank') return;
    if (!href.startsWith('/') && !href.startsWith('#')) return;
    event.preventDefault();
    navigate(href.startsWith('#') ? { hash: href } : href);
  };

  return (
    <div
      onClick={handleClick}
      className="prose prose-sm sm:prose-base max-w-none text-gray-700 prose-headings:text-gray-900 prose-h1:text-3xl prose-h1:font-bold prose-a:text-accent prose-a:no-underline hover:prose-a:underline prose-code:before:content-none prose-code:after:content-none prose-code:rounded prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:font-normal prose-table:text-sm"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
