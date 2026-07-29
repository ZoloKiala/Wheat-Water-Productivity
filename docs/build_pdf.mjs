/* Render the project documentation to PDF for handover.
 *
 *   cd docs
 *   npm install marked playwright && npx playwright install chromium
 *   node build_pdf.mjs
 *
 * Writes docs/pdf/*.pdf. Chromium's print pipeline does the typesetting, so the
 * PDFs match what the Markdown renders to, with a cover block, running footer
 * and page numbers added by the print stylesheet.
 */

import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { marked } from 'marked'
import { chromium } from 'playwright'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const OUT = join(HERE, 'pdf')

const DOCS = [
  { src: join(ROOT, 'README.md'), out: 'WWP-Dashboard-Overview.pdf',
    title: 'Wheat Water Productivity Dashboard', subtitle: 'Project overview, setup and API reference' },
  { src: join(HERE, 'ARCHITECTURE.md'), out: 'WWP-Dashboard-Architecture.pdf',
    title: 'System Architecture', subtitle: 'Wheat Water Productivity Dashboard' },
  { src: join(HERE, 'TOR_COVERAGE.md'), out: 'WWP-Dashboard-ToR-Coverage.pdf',
    title: 'Terms of Reference Coverage', subtitle: 'Wheat Water Productivity Dashboard' },
]

const ORG = 'Ethiopian Institute of Agricultural Research · IWMI East Africa · FAO WaPOR Phase II'

const CSS = `
  @page {
    size: A4;
    margin: 20mm 18mm 18mm;
    @bottom-center { content: counter(page); }
  }
  * { box-sizing: border-box; }
  body {
    font-family: "Public Sans", "Segoe UI", system-ui, sans-serif;
    font-size: 10.5pt; line-height: 1.55; color: #1c2b22; margin: 0;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
  .cover { border-bottom: 3px solid #1e6b3f; padding-bottom: 14pt; margin-bottom: 22pt; }
  .cover .mark { font-size: 8pt; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: #27824d; margin-bottom: 8pt; }
  .cover h1 { font-size: 22pt; font-weight: 800; line-height: 1.15; margin: 0 0 5pt; color: #14432b; }
  .cover .sub { font-size: 11pt; color: #5f6f64; font-weight: 500; }
  .cover .org { font-size: 8.5pt; color: #8a988f; margin-top: 10pt; line-height: 1.4; }

  h1, h2, h3, h4 { color: #14432b; font-weight: 700; break-after: avoid; page-break-after: avoid; }
  h1 { font-size: 16pt; margin: 20pt 0 7pt; padding-bottom: 4pt; border-bottom: 1px solid #dfe4da; }
  h2 { font-size: 13pt; margin: 17pt 0 6pt; }
  h3 { font-size: 11pt; margin: 13pt 0 4pt; }
  h4 { font-size: 10pt; margin: 11pt 0 3pt; color: #1c2b22; }
  p { margin: 0 0 7pt; }
  ul, ol { margin: 0 0 8pt; padding-left: 17pt; }
  li { margin-bottom: 3pt; }
  strong { font-weight: 700; color: #14432b; }
  a { color: #1e6b3f; text-decoration: none; }

  code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 9pt;
    background: #f1f4ef; padding: 1pt 3pt; border-radius: 2pt; color: #14432b; }
  pre { background: #f4f5f1; border: 1px solid #dfe4da; border-left: 3px solid #27824d;
    border-radius: 3pt; padding: 8pt 10pt; margin: 0 0 9pt; overflow: visible;
    break-inside: avoid; page-break-inside: avoid; }
  pre code { background: none; padding: 0; font-size: 8.6pt; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word; }

  table { width: 100%; border-collapse: collapse; margin: 0 0 10pt; font-size: 9pt;
    break-inside: avoid; page-break-inside: avoid; }
  th, td { text-align: left; padding: 4pt 6pt; border-bottom: 1px solid #dfe4da;
    vertical-align: top; }
  th { background: #eef3ea; color: #14432b; font-weight: 700; font-size: 8.5pt;
    text-transform: uppercase; letter-spacing: .04em; border-bottom: 1.5px solid #cfdcc9; }
  tbody tr:nth-child(even) { background: #fafbf9; }

  blockquote { margin: 0 0 9pt; padding-left: 10pt; border-left: 3px solid #d9a03f;
    color: #5f6f64; }
  hr { border: 0; border-top: 1px solid #dfe4da; margin: 14pt 0; }
  /* Long tables and wide code must never be clipped at the page edge. */
  img { max-width: 100%; }
`

function coverHtml(title, subtitle, dateStr) {
  return `<div class="cover">
    <div class="mark">Technical documentation</div>
    <h1>${title}</h1>
    <div class="sub">${subtitle}</div>
    <div class="org">${ORG}<br>Generated ${dateStr}</div>
  </div>`
}

/* Cross-references between the Markdown files must point at the sibling PDFs —
   and read as document names, since a reader holding the PDF has no .md file. */
const LINK_MAP = {
  'README.md': { file: 'WWP-Dashboard-Overview.pdf', name: 'the Overview document' },
  'ARCHITECTURE.md': { file: 'WWP-Dashboard-Architecture.pdf', name: 'the Architecture document' },
  'docs/ARCHITECTURE.md': { file: 'WWP-Dashboard-Architecture.pdf', name: 'the Architecture document' },
  'TOR_COVERAGE.md': { file: 'WWP-Dashboard-ToR-Coverage.pdf', name: 'the ToR Coverage document' },
  'docs/TOR_COVERAGE.md': { file: 'WWP-Dashboard-ToR-Coverage.pdf', name: 'the ToR Coverage document' },
}

function rewriteLinks(html) {
  return html.replace(/<a href="([^"]+)">([\s\S]*?)<\/a>/g, (m, href, text) => {
    const [path, hash] = href.split('#')
    const hit = LINK_MAP[path]
    if (!hit) return m
    // A label that is just the filename becomes the document name; a filename
    // embedded in a longer label (".../ARCHITECTURE.md § Palette") is swapped in
    // place. Prose labels like "the work-package mapping" are left alone.
    const label = /\.md$/.test(text.trim())
      ? hit.name
      : text.replace(/(?:docs\/)?(?:README|ARCHITECTURE|TOR_COVERAGE)\.md/g, hit.name)
    return `<a href="${hit.file}${hash ? '#' + hash : ''}">${label}</a>`
  })
}

const dateStr = process.env.DOC_DATE || new Date().toISOString().slice(0, 10)

await mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const page = await browser.newPage()

for (const doc of DOCS) {
  const md = await readFile(doc.src, 'utf8')
  const body = rewriteLinks(marked.parse(md, { mangle: false, headerIds: false }))
  const html = `<!doctype html><html><head><meta charset="utf-8">
    <title>${doc.title}</title><style>${CSS}</style></head>
    <body>${coverHtml(doc.title, doc.subtitle, dateStr)}${body}</body></html>`

  // KEEP_HTML=1 saves the intermediate HTML, which is how the print styling is
  // inspected — headless Chromium downloads PDFs instead of rendering them.
  if (process.env.KEEP_HTML) {
    await writeFile(join(OUT, doc.out.replace('.pdf', '.html')), html, 'utf8')
  }
  await page.setContent(html, { waitUntil: 'load' })
  const target = join(OUT, doc.out)
  await page.pdf({
    path: target,
    format: 'A4',
    printBackground: true,
    margin: { top: '20mm', bottom: '18mm', left: '18mm', right: '18mm' },
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: `<div style="width:100%;font-family:'Segoe UI',sans-serif;font-size:7.5pt;
      color:#8a988f;padding:0 18mm;display:flex;justify-content:space-between;">
      <span>${doc.title}</span>
      <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
    </div>`,
  })
  console.log(`  ${doc.out}`)
}

await browser.close()
await writeFile(join(OUT, 'README.txt'),
  'Generated from the Markdown sources in docs/ and README.md.\n' +
  'Regenerate with:  cd docs && node build_pdf.mjs\n', 'utf8')
console.log(`\nWrote ${DOCS.length} PDFs to docs/pdf/`)
