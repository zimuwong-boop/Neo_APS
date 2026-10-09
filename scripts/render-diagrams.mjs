import { createServer } from 'node:http'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, extname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../frontend/node_modules/playwright/index.mjs'

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packageRoot = join(project, '.tools/doc-render/node_modules')
const output = join(project, 'doc/diagrams/rendered')
await mkdir(output, { recursive: true })
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
  if (pathname === '/') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8')
    response.end('<!doctype html><html><body></body></html>')
    return
  }
  const path = resolve(packageRoot, '.' + pathname)
  if (!path.startsWith(packageRoot + sep)) { response.writeHead(403); response.end(); return }
  try {
    response.setHeader('Content-Type', ['.mjs', '.js'].includes(extname(path)) ? 'text/javascript' : 'application/octet-stream')
    response.end(await readFile(path))
  } catch { response.writeHead(404); response.end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const browser = await chromium.launch({ headless: true })
let count = 0
try {
  const address = server.address()
  const page = await browser.newPage({ viewport: { width: 1800, height: 1000 } })
  await page.goto(`http://127.0.0.1:${address.port}/`)
  await page.evaluate(async () => {
    window.diagramMermaid = (await import('/mermaid/dist/mermaid.esm.min.mjs')).default
    window.diagramMermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'Microsoft YaHei, Arial, sans-serif', maxTextSize: 200000 })
  })
  for (const filename of (await readdir(join(project, 'doc'))).filter(name => name.endsWith('.md')).sort()) {
    const source = await readFile(join(project, 'doc', filename), 'utf8')
    let index = 0
    for (const match of source.matchAll(/```mermaid\r?\n([\s\S]*?)```/g)) {
      index += 1
      const name = `${filename.slice(0, -3)}-${String(index).padStart(2, '0')}`
      const svg = await page.evaluate(async ({ definition, id }) => {
        const result = await window.diagramMermaid.render(id, definition)
        return result.svg
      }, { definition: match[1], id: `diagram${count}` })
      await writeFile(join(output, `${name}.mmd`), match[1], 'utf8')
      await writeFile(join(output, `${name}.svg`), svg, 'utf8')
      await page.evaluate(svg => { document.body.innerHTML = svg; document.body.style.margin = '24px'; document.body.style.background = 'white'; const drawing = document.querySelector('svg'); drawing.style.width = '1750px'; drawing.style.maxWidth = '1750px'; drawing.style.height = 'auto' }, svg)
      await page.locator('svg').screenshot({ path: join(output, `${name}.png`) })
      count += 1
      process.stdout.write(`Rendered ${name}\n`)
    }
  }
  process.stdout.write(`Validated and rendered ${count} Mermaid diagrams.\n`)
} finally {
  await browser.close()
  await new Promise(resolve => server.close(resolve))
}
