<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import OperationsWorkspace from './OperationsWorkspace.vue'

interface Item { id: number; code: string; name: string; kind: string; unit: string; traceability_info: string; is_active: boolean; revision?: number; specification?: string; product_standard?: string; allergens?: string; shelf_life_days?: number | null; storage_condition?: string }
interface Line { id?: number; item?: number; component?: number; quantity: string }
interface Document { id: number; number?: string; customer_name?: string; priority?: number; due_date?: string | null; item?: number; version?: number; output_qty?: string; status: string; lines: Line[]; revision?: number; progress?: { line_id: number; item: number; percent: number; display_percent: number; demo_active: boolean; fulfilled: string; quantity: string; remaining: string }[] }
type Tab = 'items' | 'boms' | 'sales-orders' | 'operations'
const labels: Record<string, string> = { FINISHED: '成品', SEMI: '半成品', RAW: '原料', DRAFT: '草稿', ACTIVE: '生效', RETIRED: '停用', CONFIRMED: '已确认', CANCELLED: '已取消', COMPLETED: '已完成' }
const state = ref('正在检查连接…')
const ready = ref(false)
const username = ref<string | null>(null)
const csrf = ref('')
const loginName = ref('admin')
const password = ref('')
const busy = ref(false)
const error = ref('')
const tab = ref<Tab>('items')
const items = ref<Item[]>([])
const documents = ref<Document[]>([])
const dialog = ref(false)
const editing = ref<number | null>(null)
const editingRevision = ref<number | undefined>()
const itemForm = ref<Omit<Item, 'id'>>({ code: '', name: '', kind: 'SEMI', unit: '件', traceability_info: '', is_active: true })
const form = ref({ number: '', customer_name: '', priority: 3, due_date: '', item: undefined as number | undefined, version: 1, output_qty: '1.000000', lines: [] as Line[] })
const sellable = computed(() => items.value.filter(item => item.is_active && item.kind !== 'RAW'))
const components = computed(() => items.value.filter(item => item.is_active && item.kind !== 'FINISHED'))
const title = computed(() => ({ items: '物料', boms: 'BOM', 'sales-orders': '销售单', operations: '生产排产' })[tab.value])
const itemName = (id?: number) => { const item = items.value.find(item => item.id === id); return item ? `${item.code} · ${item.name}` : String(id ?? '') }
const treeDialog = ref(false)
const tree = ref<{ root: number; nodes: Item[]; edges: { parent: number; component: number; quantity: string; output_qty: string; version: number }[] } | null>(null)
const treePath = ref<number[]>([])
const currentEdges = computed(() => tree.value?.edges.filter(edge => edge.parent === treePath.value[treePath.value.length - 1]) ?? [])
async function showTree(row: Document) { await run(async () => { tree.value = await api(`boms/${row.id}/tree/`); treePath.value = [tree.value!.root]; treeDialog.value = true }) }

async function api(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(`/api/v1/${path}`, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.value }, body: body === undefined ? undefined : JSON.stringify(body) })
  const data = response.status === 204 ? null : response.headers.get('content-type')?.includes('application/json') ? await response.json() : { detail: `请求失败（${response.status}），请刷新页面后重试。` }
  if (!response.ok) {
    const messages = (value: unknown): string => typeof value === 'string' ? value : Array.isArray(value) ? value.map(messages).join('；') : value && typeof value === 'object' ? Object.values(value).map(messages).join('；') : String(value)
    throw new Error(messages(data))
  }
  return data
}
async function refresh() {
  items.value = await api('items/')
  if (tab.value === 'boms' || tab.value === 'sales-orders') documents.value = await api(`${tab.value}/`)
}
async function run(operation: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try { await operation() } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败' } finally { busy.value = false }
}
async function signIn() {
  await run(async () => { const data = await api('auth/login/', 'POST', { username: loginName.value, password: password.value }); username.value = data.username; csrf.value = data.csrfToken; password.value = ''; await refresh() })
}
async function signOut() {
  await run(async () => { await api('auth/logout/', 'POST'); username.value = null; const data = await api('auth/session/'); csrf.value = data.csrfToken })
}
function open(row?: Item | Document) {
  error.value = ''
  editing.value = row?.id ?? null
  editingRevision.value = row?.revision
  if (tab.value === 'items') itemForm.value = row ? { ...(row as Item) } : { code: '', name: '', kind: 'SEMI', unit: '件', traceability_info: '', is_active: true }
  else {
    const doc = row as Document | undefined
    form.value = { number: doc?.number ?? '', customer_name: doc?.customer_name ?? '', priority: doc?.priority ?? 3, due_date: doc?.due_date ?? '', item: doc?.item, version: doc?.version ?? 1, output_qty: doc?.output_qty ?? '1.000000', lines: doc ? doc.lines.map(line => ({ ...line })) : [{ quantity: '1.000000' }] }
  }
  dialog.value = true
}
async function save() {
  await run(async () => {
    const body = tab.value === 'items' ? itemForm.value : tab.value === 'boms' ? { item: form.value.item, version: form.value.version, output_qty: form.value.output_qty, lines: form.value.lines.map(line => ({ component: line.component, quantity: line.quantity })) } : { number: form.value.number, customer_name: form.value.customer_name, priority: form.value.priority, due_date: form.value.due_date || null, lines: form.value.lines.map(line => ({ item: line.item, quantity: line.quantity })) }
    if (tab.value === 'sales-orders') (body as { lines: Line[] }).lines = form.value.lines.map(line => ({ ...(line.id ? { id: line.id } : {}), item: line.item, quantity: line.quantity }))
    await api(`${tab.value}/${editing.value === null ? '' : editing.value + '/'}`, editing.value === null ? 'POST' : 'PUT', { ...body, ...(editingRevision.value ? { revision: editingRevision.value } : {}) })
    dialog.value = false
    await refresh()
    ElMessage.success('已保存')
  })
}
async function operate(row: Item | Document, action: string) {
  if (action === 'clone') { await run(async () => { await api(`boms/${row.id}/clone/`, 'POST'); await refresh(); ElMessage.success('已复制为新版本草稿') }); return }
  try { await ElMessageBox.confirm(`确认${action === 'delete' ? '删除' : action === 'publish' ? '发布' : action === 'confirm' ? '确认' : '取消'}这条记录？`, '确认操作', { type: 'warning' }) } catch { return }
  await run(async () => { await api(`${tab.value}/${row.id}/${action === 'delete' ? '' : action + '/'}`, action === 'delete' ? 'DELETE' : 'POST'); await refresh() })
}
onMounted(async () => {
  await run(async () => {
    const health = await api('health/'); ready.value = health.status === 'ok'; state.value = ready.value ? '应用与数据库连接正常' : '数据库暂未就绪'
    const session = await api('auth/session/'); csrf.value = session.csrfToken; username.value = session.username
    if (username.value) await refresh()
  })
})
async function refreshSales() {
  if (username.value && tab.value === 'sales-orders' && !busy.value && !dialog.value) {
    try { await refresh(); if (error.value.startsWith('连接中断')) error.value = '' } catch { error.value = '连接中断，销售进度显示旧数据；正在重试。' }
  }
}
const poll: ReturnType<typeof setInterval> = setInterval(refreshSales, 2000)
const onVisible = () => { if (document.visibilityState === 'visible') void refreshSales() }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onUnmounted(() => { clearInterval(poll); document.removeEventListener('visibilitychange', onVisible) })
</script>

<template>
  <main>
    <header><div><p class="brand">NEO APS</p><h1>定制化生产排产</h1></div><el-button v-if="username" @click="signOut" :disabled="busy">{{ username }} · 退出</el-button></header>
    <el-alert :title="state" :type="ready ? 'success' : 'info'" :closable="false" />
    <el-alert v-if="error" class="error" :title="error" type="error" :closable="false" />
    <el-form v-if="!username" class="login" label-position="top" @submit.prevent="signIn">
      <h2>登录管理系统</h2><el-form-item label="用户名"><el-input v-model="loginName" autocomplete="username" /></el-form-item>
      <el-form-item label="密码"><el-input v-model="password" type="password" show-password autocomplete="current-password" /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy">登录</el-button>
    </el-form>
    <section v-else>
      <el-tabs v-model="tab" @tab-change="run(refresh)"><el-tab-pane label="物料" name="items" /><el-tab-pane label="BOM" name="boms" /><el-tab-pane label="销售单" name="sales-orders" /><el-tab-pane label="排产与生产" name="operations" /></el-tabs>
      <OperationsWorkspace v-if="tab === 'operations'" :api="api" :items="items" />
      <div v-if="tab !== 'operations'" class="toolbar"><h2>{{ title }}管理</h2><el-button type="primary" @click="open()">新增{{ title }}</el-button></div>
      <el-table v-if="tab === 'items'" :data="items" stripe empty-text="请先新增物料">
        <el-table-column prop="code" label="编码" /><el-table-column prop="name" label="名称" />
        <el-table-column label="类型"><template #default="{ row }">{{ labels[row.kind] }}</template></el-table-column>
        <el-table-column prop="unit" label="单位" /><el-table-column prop="traceability_info" label="溯源信息" show-overflow-tooltip />
        <el-table-column prop="specification" label="规格" /><el-table-column prop="product_standard" label="标准参考" show-overflow-tooltip />
        <el-table-column label="状态"><template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button link type="primary" @click="open(row)">编辑</el-button><el-button link type="danger" @click="operate(row, 'delete')">删除</el-button></template></el-table-column>
      </el-table>
      <el-table v-else-if="tab !== 'operations'" :data="documents" stripe empty-text="暂无记录">
        <el-table-column v-if="tab === 'sales-orders'" prop="number" label="单号" />
        <el-table-column v-if="tab === 'sales-orders'" prop="customer_name" label="客户" />
        <el-table-column v-if="tab === 'sales-orders'" label="优先级"><template #default="{ row }">P{{ row.priority }}{{ row.priority === 1 ? ' · 最高' : '' }}</template></el-table-column>
        <el-table-column v-if="tab === 'sales-orders'" prop="due_date" label="交期" />
        <el-table-column v-if="tab === 'boms'" label="主件"><template #default="{ row }">{{ itemName(row.item) }}</template></el-table-column>
        <el-table-column v-if="tab === 'boms'" prop="version" label="版本" /><el-table-column v-if="tab === 'boms'" prop="output_qty" label="基准产量" />
        <el-table-column label="明细"><template #default="{ row }"><div v-for="(line, i) in row.lines" :key="i">{{ itemName(line.item ?? line.component) }} × {{ line.quantity }}</div></template></el-table-column>
        <el-table-column label="状态"><template #default="{ row }">{{ labels[row.status] }}</template></el-table-column>
        <el-table-column v-if="tab === 'sales-orders'" label="生产进度" min-width="200"><template #default="{ row }"><div v-for="line in row.progress" :key="line.line_id">{{ itemName(line.item) }} · 真实报工 {{ line.fulfilled }} / {{ line.quantity }}<el-progress :percentage="line.display_percent" /><el-tag v-if="line.demo_active" size="small" type="warning">含演示进度 · 真实 {{ Number(line.percent.toFixed(2)) }}%</el-tag><small>待排 {{ line.remaining }}</small></div></template></el-table-column>
        <el-table-column label="操作" width="260"><template #default="{ row }">
          <el-button v-if="tab === 'boms'" link type="primary" @click="showTree(row)">逐层展开</el-button><el-button v-if="tab === 'boms'" link type="primary" @click="operate(row, 'clone')">复制版本</el-button>
          <el-button v-if="tab === 'sales-orders' && row.status === 'CONFIRMED'" link type="primary" @click="open(row)">编辑</el-button>
          <template v-if="row.status === 'DRAFT'"><el-button link type="primary" @click="open(row)">编辑</el-button><el-button link type="primary" @click="operate(row, tab === 'boms' ? 'publish' : 'confirm')">{{ tab === 'boms' ? '发布' : '确认' }}</el-button><el-button link type="danger" @click="operate(row, 'delete')">删除</el-button></template>
          <el-button v-if="tab === 'sales-orders' && ['DRAFT', 'CONFIRMED'].includes(row.status)" link type="warning" @click="operate(row, 'cancel')">取消</el-button>
        </template></el-table-column>
      </el-table>
      <p v-if="tab !== 'operations'" class="note">优先级 P1 最高、P5 最低。确认销售单后，在“排产与生产”选择多个订单生成建议；销售进度每 2 秒刷新。</p>
    </section>
    <el-dialog v-model="dialog" :title="`${editing === null ? '新增' : '编辑'}${title}`" width="680px" :close-on-click-modal="false">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-form label-width="100px" @submit.prevent="save">
        <template v-if="tab === 'items'">
          <el-form-item label="编码"><el-input v-model="itemForm.code" /></el-form-item><el-form-item label="名称"><el-input v-model="itemForm.name" /></el-form-item>
          <el-form-item label="类型"><el-select v-model="itemForm.kind"><el-option v-for="kind in ['FINISHED', 'SEMI', 'RAW']" :key="kind" :value="kind" :label="labels[kind]" /></el-select></el-form-item>
          <el-form-item label="单位"><el-input v-model="itemForm.unit" /></el-form-item><el-form-item label="溯源信息"><el-input v-model="itemForm.traceability_info" type="textarea" /></el-form-item><el-form-item label="启用"><el-switch v-model="itemForm.is_active" /></el-form-item>
          <el-form-item label="规格"><el-input v-model="itemForm.specification" /></el-form-item><el-form-item label="标准参考"><el-input v-model="itemForm.product_standard" /></el-form-item><el-form-item label="致敏物质"><el-input v-model="itemForm.allergens" /></el-form-item><el-form-item label="保质期(天)"><el-input-number v-model="itemForm.shelf_life_days" :min="1" :precision="0" /></el-form-item><el-form-item label="储存条件"><el-input v-model="itemForm.storage_condition" /></el-form-item>
        </template>
        <template v-else>
          <template v-if="tab === 'sales-orders'">
            <el-form-item label="单号"><el-input v-model="form.number" /></el-form-item><el-form-item label="客户"><el-input v-model="form.customer_name" /></el-form-item>
            <el-form-item label="优先级"><el-select v-model="form.priority"><el-option v-for="p in 5" :key="p" :value="p" :label="`P${p}${p === 1 ? ' · 最高' : ''}`" /></el-select></el-form-item><el-form-item label="交期"><el-date-picker v-model="form.due_date" value-format="YYYY-MM-DD" /></el-form-item>
          </template>
          <template v-else>
            <el-form-item label="主件"><el-select v-model="form.item" filterable><el-option v-for="item in sellable" :key="item.id" :value="item.id" :label="itemName(item.id)" /></el-select></el-form-item>
            <el-form-item label="版本"><el-input-number v-model="form.version" :min="1" :precision="0" /></el-form-item><el-form-item label="基准产量"><el-input v-model="form.output_qty" /></el-form-item>
          </template>
          <div v-for="(line, i) in form.lines" :key="i" class="line">
            <el-select v-if="tab === 'boms'" v-model="line.component" filterable placeholder="组成物料"><el-option v-for="item in components" :key="item.id" :value="item.id" :label="itemName(item.id)" /></el-select>
            <el-select v-else v-model="line.item" filterable placeholder="销售物料"><el-option v-for="item in sellable" :key="item.id" :value="item.id" :label="itemName(item.id)" /></el-select>
            <el-input v-model="line.quantity" placeholder="数量" /><el-button @click="form.lines.splice(i, 1)">移除</el-button>
          </div>
          <el-button @click="form.lines.push({ quantity: '1.000000' })">添加明细</el-button>
        </template>
      </el-form>
      <template #footer><el-button @click="dialog = false">关闭</el-button><el-button type="primary" :loading="busy" @click="save">保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="treeDialog" title="BOM 逐层展开" width="850px"><template v-if="tree"><el-breadcrumb><el-breadcrumb-item v-for="(id, index) in treePath" :key="index"><el-button link @click="treePath = treePath.slice(0, index + 1)">{{ tree.nodes.find(item => item.id === id)?.name }}</el-button></el-breadcrumb-item></el-breadcrumb><p>当前显示第 {{ treePath.length }} 层；点击半成品继续展开。路径复用会分别展示，不限制层级。</p><el-table :data="currentEdges"><el-table-column label="组件"><template #default="{ row }">{{ tree.nodes.find(item => item.id === row.component)?.name }}</template></el-table-column><el-table-column label="类型"><template #default="{ row }">{{ labels[tree.nodes.find(item => item.id === row.component)?.kind ?? ''] }}</template></el-table-column><el-table-column prop="quantity" label="用量" /><el-table-column prop="output_qty" label="主件基准产量" /><el-table-column prop="version" label="BOM 版本" /><el-table-column label="操作"><template #default="{ row }"><el-button v-if="tree.nodes.find(item => item.id === row.component)?.kind === 'SEMI'" link type="primary" @click="treePath.push(row.component)">展开</el-button></template></el-table-column></el-table></template></el-dialog>
  </main>
</template>

<style>
body { margin: 0; background: #f4f7fb; color: #152237; font-family: system-ui, sans-serif; }
main { max-width: 1200px; margin: 32px auto; padding: 32px; background: white; border-radius: 16px; }
header, .toolbar { display: flex; align-items: center; justify-content: space-between; }
.brand { color: #3270ca; font-weight: 700; letter-spacing: 3px; margin: 0; }
h1 { font-size: 28px; }.login { max-width: 360px; margin: 40px auto; }
.error, section { margin-top: 20px; }.note { color: #66758a; font-size: 14px; margin-top: 24px; }
.line { display: flex; gap: 12px; margin: 12px 0; }.line .el-select { flex: 2; }.line .el-input { flex: 1; }
@media (max-width: 700px) { main { margin: 0; padding: 16px; }.line { flex-wrap: wrap; } }
</style>
