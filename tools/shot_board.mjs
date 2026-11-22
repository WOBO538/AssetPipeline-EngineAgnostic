// 用已开的 Chrome（CDP 9333）打开本地看板 HTML，截三张图并收集 JS 报错。
// 用法: node tools/shot_board.mjs <htmlPath> [outDir]
import { createRequire } from 'node:module'
import path from 'node:path'
import fs from 'node:fs'
const require = createRequire(import.meta.url)
const { chromium } = require('C:/Users/ASUS/.dsh/dsh-browser/node_modules/.pnpm/node_modules/playwright-core')

const htmlPath = process.argv[2]
const outDir = process.argv[3] || 'D:/Projects/AssetPipeline-EngineAgnostic/docs'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
fs.mkdirSync(outDir, { recursive: true })

const fileUrl = 'file:///' + htmlPath.replace(/\\/g, '/')
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333')
const ctx = browser.contexts()[0]
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()) })

await page.setViewportSize({ width: 1440, height: 1000 })
await page.goto(fileUrl, { waitUntil: 'load' })
await sleep(800)

const shots = { overview: 'board_overview.png', gantt: 'board_gantt.png', kanban: 'board_kanban.png' }
for (const [tab, name] of Object.entries(shots)) {
  await page.click(`nav button[data-tab="${tab}"]`)
  await sleep(500)
  const out = path.join(outDir, name)
  await page.screenshot({ path: out, fullPage: true })
  console.log('shot', name, fs.statSync(out).size, 'bytes')
}
const counts = await page.evaluate(() => ({
  gates: document.querySelectorAll('#gates .row').length,
  projects: document.querySelectorAll('#projects .proj').length,
  materials: document.querySelectorAll('#materials .m').length,
  reviews: document.querySelectorAll('#reviews .row').length,
  ganttRows: document.querySelectorAll('#grows .grow').length,
  kanbanCards: document.querySelectorAll('#kcols .kcard').length,
  textLen: document.body.innerText.length,
}))
console.log('COUNTS ' + JSON.stringify(counts))
console.log('JS_ERRORS ' + (errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'))
await page.close()
process.exit(errors.length ? 1 : 0)
