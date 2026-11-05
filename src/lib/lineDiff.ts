export type DiffLine =
  | { kind: 'same' | 'del' | 'add'; text: string; oldNo?: number; newNo?: number }
  | { kind: 'skip'; count: number };

// Past this many line pairs the LCS table gets too big for the browser, so the
// diff falls back to "everything removed, everything added".
const MAX_CELLS = 4_000_000;

/** Line-level diff of two texts (longest common subsequence). */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const out: DiffLine[] = [];
  for (let i = 0; i < start; i++) out.push({ kind: 'same', text: a[i], oldNo: i + 1, newNo: i + 1 });

  const xs = a.slice(start, endA);
  const ys = b.slice(start, endB);
  const n = xs.length;
  const m = ys.length;
  if (n * m > MAX_CELLS) {
    xs.forEach((text, i) => out.push({ kind: 'del', text, oldNo: start + i + 1 }));
    ys.forEach((text, j) => out.push({ kind: 'add', text, newNo: start + j + 1 }));
  } else {
    const width = m + 1;
    const lcs = new Uint32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * width + j] =
          xs[i] === ys[j] ? lcs[(i + 1) * width + j + 1] + 1 : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && xs[i] === ys[j]) {
        out.push({ kind: 'same', text: xs[i], oldNo: start + i + 1, newNo: start + j + 1 });
        i++;
        j++;
      } else if (j < m && (i >= n || lcs[i * width + j + 1] >= lcs[(i + 1) * width + j])) {
        out.push({ kind: 'add', text: ys[j], newNo: start + j + 1 });
        j++;
      } else {
        out.push({ kind: 'del', text: xs[i], oldNo: start + i + 1 });
        i++;
      }
    }
  }

  const shift = endB - endA;
  for (let i = endA; i < a.length; i++) out.push({ kind: 'same', text: a[i], oldNo: i + 1, newNo: i + shift + 1 });
  return out;
}

/** Collapse unchanged runs longer than ``2 * context`` lines into a skip marker. */
export function collapseUnchanged(lines: DiffLine[], context = 3): DiffLine[] {
  const out: DiffLine[] = [];
  let run: DiffLine[] = [];
  const flush = (atStart: boolean, atEnd: boolean) => {
    const keepHead = atStart ? 0 : context;
    const keepTail = atEnd ? 0 : context;
    if (run.length > keepHead + keepTail + 1) {
      out.push(...run.slice(0, keepHead));
      out.push({ kind: 'skip', count: run.length - keepHead - keepTail });
      out.push(...run.slice(run.length - keepTail));
    } else {
      out.push(...run);
    }
    run = [];
  };
  lines.forEach((line) => {
    if (line.kind === 'same') {
      run.push(line);
      return;
    }
    flush(out.length === 0, false);
    out.push(line);
  });
  flush(out.length === 0, true);
  return out;
}
