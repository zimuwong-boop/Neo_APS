import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

test('login and create a material, BOM and priority sales order', async ({ page }) => {
  const credentialFile = process.env.E2E_BASE_URL?.includes(':8080') ? '.tools/access.docker.json' : '.tools/access.local.json'
  const credentials = JSON.parse(readFileSync(new URL(`../../${credentialFile}`, import.meta.url), 'utf8').replace(/^\uFEFF/, ''))
  const suffix = `${Date.now()}`
  const ids: { endpoint: string; id: number }[] = []
  await page.goto('/')
  const loginForm = page.locator('form.login')
  await loginForm.locator('input').nth(0).fill(credentials.username)
  await loginForm.locator('input').nth(1).fill(credentials.password)
  await loginForm.getByRole('button', { name: '登录', exact: true }).click()
  // Clear secrets immediately so a failed assertion's page snapshot cannot include them.
  await page.evaluate(() => {
    const input = document.querySelector<HTMLInputElement>('form.login input[type="password"]')
    if (input) input.value = ''
  })
  await expect(page.getByRole('heading', { name: '物料管理' })).toBeVisible()

  async function create(endpoint: string, body: unknown) {
    const result = await page.evaluate(async ({ endpoint, body }) => {
      const session = await (await fetch('/api/v1/auth/session/')).json()
      const response = await fetch(`/api/v1/${endpoint}/`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': session.csrfToken }, body: JSON.stringify(body) })
      return { status: response.status, data: await response.json() }
    }, { endpoint, body })
    expect(result.status).toBe(201)
    ids.push({ endpoint, id: result.data.id })
    return result.data
  }
  try {
    const raw = await create('items', { code: `E2E-RAW-${suffix}`, name: '浏览器测试原料', kind: 'RAW', unit: '件' })
    await page.getByRole('button', { name: '新增物料' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.locator('input').nth(0).fill(`E2E-SEMI-${suffix}`)
    await dialog.locator('input').nth(1).fill('浏览器测试半成品')
    await dialog.locator('textarea').fill('供应商批次 E2E')
    const saved = page.waitForResponse(response => response.url().endsWith('/items/') && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    const semiResponse = await saved
    expect(semiResponse.status()).toBe(201)
    const semi = await semiResponse.json()
    ids.push({ endpoint: 'items', id: semi.id })
    await expect(dialog).not.toBeVisible()
    await expect(page.getByText(`E2E-SEMI-${suffix}`, { exact: true })).toBeVisible()

    await page.getByRole('tab', { name: 'BOM', exact: true }).click()
    await page.getByRole('button', { name: '新增BOM' }).click()
    await dialog.locator('.el-select').nth(0).click()
    await page.getByRole('option', { name: `${semi.code} · ${semi.name}`, exact: true }).click()
    await dialog.locator('.el-select').nth(1).click()
    await page.getByRole('option', { name: `${raw.code} · ${raw.name}`, exact: true }).click()
    const bomSaved = page.waitForResponse(response => response.url().endsWith('/boms/') && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    const bomResponse = await bomSaved
    expect(bomResponse.status()).toBe(201)
    ids.push({ endpoint: 'boms', id: (await bomResponse.json()).id })
    await expect(dialog).not.toBeVisible()

    await page.getByRole('tab', { name: '销售单', exact: true }).click()
    await page.getByRole('button', { name: '新增销售单' }).click()
    await dialog.locator('input').nth(0).fill(`E2E-SO-${suffix}`)
    await dialog.locator('input').nth(1).fill('浏览器演示客户')
    await dialog.locator('.el-select').nth(0).click()
    await page.getByRole('option', { name: 'P1 · 最高', exact: true }).click()
    await dialog.locator('.el-select').nth(1).click()
    await page.getByRole('option', { name: `${semi.code} · ${semi.name}`, exact: true }).click()
    const salesSaved = page.waitForResponse(response => response.url().endsWith('/sales-orders/') && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    const salesResponse = await salesSaved
    expect(salesResponse.status()).toBe(201)
    const sales = await salesResponse.json()
    ids.push({ endpoint: 'sales-orders', id: sales.id })
    expect(sales.priority).toBe(1)
    await expect(dialog).not.toBeVisible()
    await expect(page.getByText(`E2E-SO-${suffix}`, { exact: true })).toBeVisible()
  } finally {
    for (const record of ids.reverse()) {
      const status = await page.evaluate(async ({ endpoint, id }) => {
        const session = await (await fetch('/api/v1/auth/session/')).json()
        return (await fetch(`/api/v1/${endpoint}/${id}/`, { method: 'DELETE', headers: { 'X-CSRFToken': session.csrfToken } })).status
      }, record)
      expect(status).toBe(204)
    }
  }
})
