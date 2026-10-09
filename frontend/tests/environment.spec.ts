import { expect, test } from '@playwright/test'

test('Vue page connects to Django and PostgreSQL', async ({ page, request }) => {
  const health = await request.get('/api/v1/health/')
  expect(health.status()).toBe(200)
  expect(await health.json()).toEqual({ status: 'ok' })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '定制化生产排产' })).toBeVisible()
  await expect(page.getByText('应用与数据库连接正常')).toBeVisible()
})

test('API misses return 404 rather than the Vue page', async ({ request }) => {
  expect((await request.get('/api/v1/not-a-route/')).status()).toBe(404)
})
