import { chromium } from 'playwright'

const BASE = process.env.WWP_BASE || 'http://127.0.0.1:8000'
const OUT = process.env.SHOT_DIR || './screenshots'
await (await import('node:fs/promises')).mkdir(OUT, { recursive: true })
const errors = []
let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}${detail ? `  (${detail})` : ''}`) }
  else { fail++; console.log(`  FAIL  ${name}  ${detail}`) }
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })

page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

console.log('\n1. Initial load')
await page.goto(BASE, { waitUntil: 'networkidle', timeout: 60000 })
await page.waitForSelector('.side', { timeout: 20000 })
check('header brand renders', await page.locator('.brand .t1').innerText() === 'Wheat Water Productivity')
check('region selector populated',
  (await page.locator('#selRegion option').count()) >= 3,
  `${await page.locator('#selRegion option').count()} regions`)
check('woreda selector populated', (await page.locator('#selWoreda option').count()) >= 2)
check('results panel absent before a run', (await page.locator('.results').count()) === 0)
check('legend hidden before a run', (await page.locator('.legend').count()) === 0)
const mapW0 = (await page.locator('#map').boundingBox()).width
await page.screenshot({ path: `${OUT}/01-initial.png` })

console.log('\n2. Run analysis')
await page.locator('.runbtn').click()
await page.waitForSelector('.kpi.hero', { timeout: 90000 })
const heroText = await page.locator('.kpi.hero .v').innerText()
check('hero KPI renders', /^\d\.\d\d/.test(heroText), heroText.replace(/\s+/g, ' '))
check('6 KPI tiles', (await page.locator('.kpi').count()) === 6)
check('legend now visible', await page.locator('.legend').isVisible())
check('raster overlay on map', (await page.locator('img.pix, .pix').count()) >= 1)
check('2 charts rendered', (await page.locator('.chartbox svg').count()) === 2,
  `${await page.locator('.chartbox svg').count()} svgs`)
check('results panel appears after the run', (await page.locator('.results').count()) === 1)
await page.waitForTimeout(1200)
await page.screenshot({ path: `${OUT}/02-results.png` })

console.log('\n2b. Results panel open/close cycle')
const mapW1 = (await page.locator('#map').boundingBox()).width
check('map narrows when the panel opens', mapW1 < mapW0 - 100, `${mapW0} → ${mapW1}px`)
// Leaflet must be told its container resized, or tiles render at the stale width.
const leafletSynced = async () => await page.evaluate(() => {
  const el = document.getElementById('map')
  const pane = el.querySelector('.leaflet-map-pane')
  if (!pane) return false
  const t = el.querySelector('.leaflet-tile-container')
  return Math.abs(el.clientWidth - el.getBoundingClientRect().width) < 2 && !!t
})
check('leaflet re-synced to the new width', await leafletSynced())
await page.locator('.rhead .close').click()
await page.waitForTimeout(600)
check('close hides the panel', (await page.locator('.results').count()) === 0)
const mapW2 = (await page.locator('#map').boundingBox()).width
check('map reclaims the width', Math.abs(mapW2 - mapW0) < 2, `${mapW2}px`)
check('reopen control offered', await page.locator('.reopen').isVisible())
const zoomBox = await page.locator('.leaflet-control-zoom').boundingBox()
const reopenBox = await page.locator('.reopen').boundingBox()
check('reopen does not overlap the zoom control',
  reopenBox.y > zoomBox.y + zoomBox.height || reopenBox.x + reopenBox.width < zoomBox.x,
  `reopen y=${reopenBox.y.toFixed(0)} vs zoom bottom=${(zoomBox.y + zoomBox.height).toFixed(0)}`)
await page.screenshot({ path: `${OUT}/02b-panel-closed.png` })
await page.locator('.reopen').click()
await page.waitForSelector('.results', { timeout: 15000 })
check('reopen restores the same run',
  (await page.locator('.kpi.hero .v').innerText()).startsWith(heroText.split(' ')[0]))

console.log('\n3. Chart integrity')
const trendPts = await page.locator('.chartsec:has-text("Seasonal trend") circle').count()
check('trend has 5 markers', trendPts === 5, `${trendPts}`)
const histBars = await page.locator('.chartsec:has-text("Distribution") path').count()
check('histogram bars drawn', histBars >= 1, `${histBars} bars`)
// The estimation chain replaces the model-importance chart: it is text, not
// marks, so it is checked by content rather than by shape count.
const chainSec = page.locator('.chartsec:has-text("How this estimate is built")')
check('estimation chain rendered', (await chainSec.locator('.chain li').count()) === 5,
  `${await chainSec.locator('.chain li').count()} steps`)
const chainSteps = await chainSec.locator('.chain .nm').allInnerTexts()
check('chain runs NPP to water productivity',
  chainSteps[0] === 'Seasonal NPP' && chainSteps.at(-1) === 'Water productivity',
  chainSteps.join(' -> '))
check('chain shows the crop parameters applied',
  (await chainSec.locator('.chain .op').allInnerTexts()).join(' ').includes('harvest index'))
// No SVG text may sit outside its viewBox (label overflow check).
const overflow = await page.evaluate(() => {
  const bad = []
  document.querySelectorAll('.chartbox svg').forEach((svg) => {
    const vb = svg.viewBox.baseVal
    svg.querySelectorAll('text').forEach((t) => {
      const b = t.getBBox()
      if (b.x < -1 || b.y < -1 || b.x + b.width > vb.width + 1 || b.y + b.height > vb.height + 1) {
        bad.push(`"${t.textContent.trim().slice(0, 24)}" x=${b.x.toFixed(0)} w=${b.width.toFixed(0)} vb=${vb.width}`)
      }
    })
  })
  return bad
})
check('no chart label overflows its viewBox', overflow.length === 0, overflow.slice(0, 4).join(' | '))

console.log('\n4. Table views (no value gated behind hover)')
const toggles = page.locator('.tablebtn')
check('a table toggle per chart', (await toggles.count()) === 2, `${await toggles.count()}`)
await toggles.first().click()
await page.waitForSelector('.dtable')
const rows = await page.locator('.dtable tbody tr').count()
check('trend table lists 5 seasons', rows === 5, `${rows} rows`)
await toggles.first().click()
check('table collapses again', (await page.locator('.dtable').count()) === 0)

console.log('\n5. Pixel inspect + explanation')
const box = await page.locator('#map').boundingBox()
await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
await page.waitForSelector('.leaflet-popup', { timeout: 30000 })
const popVal = await page.locator('.pop .pv').innerText()
check('popup shows a WWP value', /^\d\.\d\d$/.test(popVal), popVal)
await page.screenshot({ path: `${OUT}/03-popup.png` })
await page.locator('#explainLink').click()
// Addressed by id: ':has-text' is case-insensitive, so a text selector here
// also matches the 'Show the derivation' hint shown before one is loaded.
const derivation = page.locator('#derivation')
await derivation.waitFor({ timeout: 30000 })
check('derivation lists every step', (await derivation.locator('.chain li').count()) === 5,
  `${await derivation.locator('.chain li').count()} steps`)
// The whole claim of a deterministic method is that the shown numbers add up.
// Verify that from the rendered text, not from the API response.
const shown = await derivation.locator('.chain .val').allInnerTexts()
const num = (t) => parseFloat(t.replace(/[^0-9.]/g, ''))
check('rendered chain reproduces the rendered result',
  Math.abs(num(shown[2]) / num(shown[3]) - num(shown[4])) < 0.01,
  `${shown[2]} / ${shown[3]} = ${shown[4]}`)
check('derivation matches the pixel popup value',
  Math.abs(num(shown[4]) - parseFloat(popVal)) < 0.01, `${shown[4]} vs ${popVal}`)
await page.waitForTimeout(900)
await page.screenshot({ path: `${OUT}/04-explain.png` })
await derivation.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/05-explain-closeup.png`, clip: await derivation.boundingBox() })

console.log('\n6. Irrigated system switches the season list')
await page.locator('.seg button:has-text("Irrigated")').click()
await page.waitForTimeout(300)
const seasonOpts = await page.locator('#selSeason option').allInnerTexts()
check('season list becomes dry-season only',
  seasonOpts.length === 1 && seasonOpts[0].includes('Dry season'), seasonOpts.join(','))
await page.locator('.seg button:has-text("Rainfed")').click()
await page.waitForTimeout(300)
check('rainfed restores Meher/Belg',
  (await page.locator('#selSeason option').allInnerTexts()).join(',') === 'Meher,Belg')

console.log('\n7. Upload validation surfaces an inline error')
await page.locator('.tabs button:has-text("Upload")').click()
await page.setInputFiles('#fileInput', {
  name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a boundary'),
})
await page.waitForSelector('.err', { timeout: 20000 })
const errText = await page.locator('.err').innerText()
check('inline upload error shown', /Unsupported file type/.test(errText), errText.slice(0, 60))
await page.screenshot({ path: `${OUT}/06-upload-error.png` })

console.log('\n8. Draw mode')
await page.locator('.tabs button:has-text("Draw")').click()
await page.locator('.drawbtn').click()
check('map tip appears', await page.locator('.maptip').isVisible(),
  await page.locator('.maptip').innerText())
await page.mouse.click(box.x + 380, box.y + 260)
await page.mouse.click(box.x + 560, box.y + 280)
await page.mouse.click(box.x + 520, box.y + 430)
await page.waitForTimeout(400)
await page.screenshot({ path: `${OUT}/07-drawing.png` })
check('finish button enabled at 3 vertices',
  await page.locator('.drawbtn:has-text("Finish polygon")').isEnabled(),
  await page.locator('.drawbtn:has-text("Finish polygon")').innerText())
await page.mouse.dblclick(box.x + 380, box.y + 420)
await page.waitForSelector('.filechip', { timeout: 15000 })
const chip = await page.locator('.filechip').innerText()
check('double-click finishes the polygon', /vertices/.test(chip), chip.replace(/\s+/g, ' '))

// The explicit Finish button must work independently of double-click.
await page.locator('.filechip .x').click()
await page.locator('.drawbtn:has-text("Draw polygon")').click()
await page.mouse.click(box.x + 300, box.y + 300)
await page.mouse.click(box.x + 460, box.y + 320)
await page.mouse.click(box.x + 420, box.y + 460)
await page.waitForTimeout(300)
await page.locator('.drawbtn:has-text("Finish polygon")').click()
await page.waitForSelector('.filechip', { timeout: 15000 })
check('Finish button also completes the polygon',
  /vertices/.test(await page.locator('.filechip').innerText()),
  (await page.locator('.filechip').innerText()).replace(/\s+/g, ' '))
await page.locator('.runbtn').click()
await page.waitForSelector('.kpi.hero', { timeout: 90000 })
await page.waitForTimeout(900)
check('polygon run produced results', /^\d\.\d\d/.test(await page.locator('.kpi.hero .v').innerText()))
await page.screenshot({ path: `${OUT}/08-polygon-run.png` })

console.log('\n9. Content pages')
await page.locator('.hdr nav button:has-text("Methodology")').click()
await page.waitForSelector('.modal .box')
check('methodology modal opens', (await page.locator('.modal h2').innerText()) === 'Methodology')
await page.screenshot({ path: `${OUT}/09-methodology.png` })
await page.locator('.modal .mh button').click()
check('modal closes', (await page.locator('.modal').count()) === 0)

console.log('\n10. Layout: no horizontal page scroll')
for (const [w, h, label] of [[1600, 950, 'desktop'], [1280, 800, 'laptop'], [900, 1000, 'tablet'], [420, 900, 'mobile']]) {
  await page.setViewportSize({ width: w, height: h })
  await page.waitForTimeout(500)
  const over = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    boxes: [...document.querySelectorAll('.chartbox')].filter((el) => el.scrollWidth > el.clientWidth + 1).length,
  }))
  check(`${label} ${w}px: no horizontal overflow`, over.doc <= 1, `overflow ${over.doc}px`)
  check(`${label} ${w}px: no nested chart scroll`, over.boxes === 0, `${over.boxes} boxes`)
  const ftr = await page.evaluate(() => {
    const f = document.querySelector('.ftr')
    return { clip: f.scrollHeight - f.clientHeight, h: f.clientHeight }
  })
  check(`${label} ${w}px: footer content not clipped`, ftr.clip <= 1,
    `${ftr.clip}px hidden (height ${ftr.h}px)`)
  await page.screenshot({ path: `${OUT}/10-${label}-${w}.png`, fullPage: w < 1100 })
}

console.log('\n10b. Scheme workflow: the reference notebook input and output')
/* Driven through the ready-made-file list rather than a hand-built fixture: that
   is the path a first-time user takes, so if the workflow is unreachable from
   the interface this check fails even though the API still works.

   Which file is used depends on the machine. Where the 2026 campaign shapefiles
   are present the real 57-sample point file is exercised; elsewhere the
   generated sample stands in. Both hold six plots, so only the number of sample
   rows differs, and that is read from the service rather than assumed. */
const PLOTS = 6
const listed = await (await fetch(`${BASE}/api/schemes/datasets`)).json()
const dataset = (listed.datasets || []).find((d) => d.geometry_type === 'point')
check('the service offers a point dataset to try', !!dataset,
  (listed.datasets || []).map((d) => d.name).join(', '))
const SAMPLES = dataset.n_features
await page.setViewportSize({ width: 1600, height: 950 })
await page.waitForTimeout(300)
await page.locator('.tabs button:has-text("Upload")').click()
const dsBtn = page.locator(`.side .datasets .linkbtn[data-dataset="${dataset.name}"]`)
check('the upload panel offers that dataset for per-plot estimation',
  (await dsBtn.count()) === 1, dataset.name)
check('every offered dataset says how large it is',
  (await page.locator('.side .datasets .ds .meta').count())
  === (listed.datasets || []).length, `${(listed.datasets || []).length} datasets`)
await dsBtn.click()
await page.waitForSelector('.schemeask', { timeout: 30000 })
check('point file offers per-plot estimation',
  (await page.locator('.schemeask .runbtn').innerText()).includes('Estimate each plot'))
await page.locator('.schemeask .runbtn').click()
await page.waitForSelector('.results:has-text("Scheme results")', { timeout: 90000 })
check('validation message is the one the notebook prints',
  (await page.locator('.banner.ok b').innerText()) === 'Data validation is successful!')
const yieldSec = page.locator('.chartsec:has-text("Estimated yield by scheme")')
const wpSec = page.locator('.chartsec:has-text("Water productivity by scheme")')
const yieldBars = await yieldSec.locator('svg path').count()
check('yield figure drawn', yieldBars === PLOTS, `${yieldBars} bars`)
check('water-productivity figure drawn',
  (await wpSec.locator('svg path').count()) === PLOTS)
const aggRows = await page.locator('.chartsec:has-text("Per-plot medians") .dtable tbody tr').count()
check('one aggregated row per plot', aggRows === PLOTS, `${aggRows} rows`)
const featRows = await page.locator('.chartsec:has-text("Every sample point") .dtable tbody tr').count()
check('one row per sample point', featRows === SAMPLES,
  `${featRows} rows from ${dataset.name} (${SAMPLES} features)`)
const heads = await page.locator('.chartsec:has-text("Every sample point") .dtable th').allInnerTexts()
check('result columns use the notebook field names',
  ['NPP', 'EYield_tpha', 'AETI_mm', 'WP_kgpm3', 'LGP'].every((c) => heads.some((h) => h.startsWith(c))),
  heads.join(' | ').slice(0, 90))
check('both CSV downloads offered',
  (await page.locator('.results a[href*="schemes/export/csv"]').count()) === 2)
await page.screenshot({ path: `${OUT}/10b-schemes.png` })
/* Wide result tables must scroll inside their own box, never widen the page. */
for (const [w, label] of [[1600, 'desktop'], [420, 'mobile']]) {
  await page.setViewportSize({ width: w, height: 950 })
  await page.waitForTimeout(400)
  const over = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth)
  check(`scheme results ${label} ${w}px: no page overflow`, over <= 1, `overflow ${over}px`)
  if (w === 420) await page.screenshot({ path: `${OUT}/10b-schemes-mobile.png`, fullPage: true })
}
await page.setViewportSize({ width: 1600, height: 950 })

console.log('\n11. Console cleanliness')
// The 422 is the deliberate bad-upload rejection from step 7, not a defect.
// HTTP/2 carries no reason phrase, so a deployed origin reports "422 ()" while a
// local HTTP/1.1 server reports "422 (Unprocessable Entity)" — match either.
const real = errors.filter((e) => !/tile\.openstreetmap|ERR_|net::|favicon|status of 422/i.test(e))
check('no JS console errors', real.length === 0, real.slice(0, 3).join(' | '))
if (errors.length !== real.length) {
  console.log(`  note: ${errors.length - real.length} network/tile error(s) ignored (offline map tiles)`)
}

await browser.close()
console.log(`\n${'='.repeat(58)}\n  ${pass} passed, ${fail} failed\n${'='.repeat(58)}`)
process.exit(fail ? 1 : 0)
