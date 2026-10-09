import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  outputDir: '../.tools/playwright-results',
  use: { baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173', headless: true },
})
