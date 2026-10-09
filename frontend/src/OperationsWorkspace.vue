<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

interface Item { id: number; code: string; name: string; kind: string; unit: string; is_active: boolean }
interface Sale { id: number; number: string; priority: number; status: string; customer_name: string; progress: { line_id: number; item: number; quantity: string; planned: string; remaining: string; fulfilled: string; percent: number; display_percent: number; demo_active: boolean }[] }
interface Task { key: string; item: Item; quantity: string; priority: number; sequence: number; suggested_sequence: number; target_start: string | null; target_end: string | null; line_label: string; notes: string; adjustment_reason: string }
interface Source { number: string; priority: number }
interface Demand { task: string; kind: string; quantity: string; priority: number; source: Source; rank: number; path: string }
interface Material { item: Item & { traceability_info: string }; quantity: string }
interface Plan { id: string; name: string; status: string; revision: number; inputs: { order_ids?: number[]; line_quantities?: Record<string, string> }; data: { tasks: Task[]; allocations: Demand[]; materials: Material[]; dependencies: [string, string][] } }
interface Allocation { id: number; kind: string; quantity: string; fulfilled_qty: string; priority: number; source: Source; rank: number }
interface Report { id: string; quantity: string; batch_code: string; notes: string; reversed: boolean; reversal_of: string | null; created_at: string; distributions: { allocation: number; quantity: string }[] }
interface Order { id: string; number: string; item: number; item_name: string; item_code: string; unit: string; quantity: string; completed_qty: string; demo_progress_percent: string | null; display_progress_percent: number; actual_progress_percent: number; priority: number; sequence: number; status: string; revision: number; plan_id: string; target_start: string | null; target_end: string | null; line_label: string; notes: string; started_at: string | null; dependencies: { id: string; number: string; status: string }[]; allocations: Allocation[]; reports: Report[]; materials: Material[]; snapshot: { item: Item & { traceability_info: string }; bom: { version: number }; components: { item: Item & { traceability_info: string } }[] } }
interface Preview { token: string; allowed: boolean; tasks: { id: string; number: string; status: string }[]; affected_sales: { number: string; quantity: string }[] }
interface Queue { token: string; suggested: string[]; current: string[]; orders: Order[] }
const props = defineProps<{ api: (path: string, method?: string, body?: unknown) => Promise<any>; items: Item[] }>()
const tab = ref('planning')
const plans = ref<Plan[]>([]), orders = ref<Order[]>([]), sales = ref<Sale[]>([])
const audits = ref<{ id: number; operation: string; object_id: string; created_at: string; details: unknown }[]>([])
const busy = ref(false), error = ref(''), online = ref(true), updatedAt = ref('')
const selectedSales = ref<number[]>([]), planName = ref('跨订单调味品排产')
const planDialog = ref(false), selectedPlan = ref<Plan | null>(null)
const adjustments = ref<Task[]>([]), amounts = ref<Record<string, string>>({})
const sourceDialog = ref(false), selectedTask = ref<Task | null>(null)
const orderDialog = ref(false), editingOrder = ref<Order | null>(null)
const productionForm = ref({ item: undefined as number | undefined, quantity: '1.000000', priority: 3, target_start: null as string | null, target_end: null as string | null, line_label: '', notes: '' })
const detail = ref<Order | null>(null), detailDialog = ref(false)
const reportDialog = ref(false), reportOrder = ref<Order | null>(null)
const reportForm = ref({ quantity: '1.000000', request_key: '', batch_code: '', notes: '' })
const demoDialog = ref(false), demoOrder = ref<Order | null>(null), demoPercent = ref(0)
const preview = ref<Preview | null>(null), cancelOrder = ref<Order | null>(null), cancelDialog = ref(false)
const queue = ref<Queue | null>(null), queueDialog = ref(false), queueOrder = ref<string[]>([]), queueReason = ref('按五级优先级插入新批次')
const eligibleSales = computed(() => sales.value.filter(sale => sale.status === 'CONFIRMED' && sale.progress.some(line => Number(line.remaining) > 0)))
const sellable = computed(() => props.items.filter(item => item.is_active && item.kind !== 'RAW'))
const itemName = (id: number) => { const item = props.items.find(item => item.id === id); return item ? `${item.code} · ${item.name}` : String(id) }
const statusNames: Record<string, string> = { DRAFT: '草稿', PUBLISHED: '已发布', RELEASED: '待开工', IN_PROGRESS: '生产中', PAUSED: '暂停', COMPLETED: '已完成', CANCELLED: '已取消', CONFIRMED: '已确认' }
const kindNames: Record<string, string> = { SALE: '直接销售', COMPONENT: '内部组件', STANDALONE: '独立生产' }
const materialSummary = computed(() => {
  const totals = new Map<number, { item: Item; quantity: number }>()
  for (const material of selectedPlan.value?.data.materials ?? []) {
    const entry = totals.get(material.item.id) ?? { item: material.item, quantity: 0 }
    entry.quantity += Number(material.quantity); totals.set(material.item.id, entry)
  }
  return [...totals.values()]
})
const stats = computed(() => ({ pending: orders.value.filter(order => order.status === 'RELEASED').length, running: orders.value.filter(order => order.status === 'IN_PROGRESS').length, completed: orders.value.filter(order => order.status === 'COMPLETED').length, salesCompleted: sales.value.filter(sale => sale.status === 'COMPLETED').length }))
async function run(operation: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await operation() } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败' } finally { busy.value = false }
}
async function load() {
  const [nextPlans, nextOrders, nextSales, nextAudit] = await Promise.all([props.api('plans/'), props.api('production-orders/'), props.api('sales-orders/'), props.api('audit/')])
  plans.value = nextPlans; orders.value = nextOrders; sales.value = nextSales; audits.value = nextAudit
  if (detail.value) detail.value = orders.value.find(order => order.id === detail.value?.id) ?? null
  online.value = true; updatedAt.value = new Date().toLocaleTimeString()
}
async function generate() { await run(async () => { await props.api('plans/', 'POST', { name: planName.value, inputs: { order_ids: selectedSales.value } }); await load(); ElMessage.success('排产建议已生成') }) }
function showPlan(plan: Plan) {
  selectedPlan.value = plan; adjustments.value = plan.data.tasks.map(task => ({ ...task })); amounts.value = {}
  for (const sale of sales.value.filter(sale => plan.inputs.order_ids?.includes(sale.id))) for (const line of sale.progress) amounts.value[String(line.line_id)] = plan.inputs.line_quantities?.[String(line.line_id)] ?? line.remaining
  planDialog.value = true
}
async function savePlan(rebuild = false) {
  if (!selectedPlan.value) return
  await run(async () => {
    const payload = rebuild ? { revision: selectedPlan.value!.revision, inputs: { ...selectedPlan.value!.inputs, line_quantities: amounts.value } } : { revision: selectedPlan.value!.revision, tasks: adjustments.value.map(task => ({ key: task.key, sequence: task.sequence, adjustment_reason: task.adjustment_reason, target_start: task.target_start, target_end: task.target_end, line_label: task.line_label, notes: task.notes })) }
    const updated = await props.api(`plans/${selectedPlan.value!.id}/`, 'PATCH', payload); await load(); showPlan(updated); ElMessage.success(rebuild ? '已按来源量重算，请复核顺序' : '调整已保存')
  })
}
async function showQueue() { await run(async () => { queue.value = await props.api('production-orders/queue/'); queueOrder.value = [...queue.value!.suggested]; queueDialog.value = true }) }
async function publish(plan: Plan) {
  try { await ElMessageBox.confirm('将完整方案发布为生产批次，数量与回分顺序将冻结。', '发布生产', { type: 'warning' }) } catch { return }
  await run(async () => { await props.api(`plans/${plan.id}/publish/`, 'POST'); planDialog.value = false; await load(); ElMessage.success('批次已发布；请复核全局插入建议') }); if (!error.value) await showQueue()
}
async function removePlan(plan: Plan) {
  try { await ElMessageBox.confirm('删除该草稿方案及关联草稿批次？', '删除草稿') } catch { return }
  await run(async () => { await props.api(`plans/${plan.id}/`, 'DELETE'); planDialog.value = false; await load() })
}
function openProduction(order?: Order) {
  editingOrder.value = order ?? null; error.value = ''
  productionForm.value = { item: order?.item, quantity: order?.quantity ?? '1.000000', priority: order?.priority ?? 3, target_start: order?.target_start ?? null, target_end: order?.target_end ?? null, line_label: order?.line_label ?? '', notes: order?.notes ?? '' }; orderDialog.value = true
}
async function saveProduction() {
  await run(async () => {
    const row = editingOrder.value
    const body = row ? { revision: row.revision, target_start: productionForm.value.target_start, target_end: productionForm.value.target_end, line_label: productionForm.value.line_label, notes: productionForm.value.notes, ...(row.status === 'DRAFT' && productionForm.value.quantity !== row.quantity ? { quantity: productionForm.value.quantity } : {}), ...(row.status === 'DRAFT' && productionForm.value.priority !== row.priority ? { priority: productionForm.value.priority } : {}) } : { ...productionForm.value }
    await props.api(`production-orders/${row ? row.id + '/' : ''}`, row ? 'PATCH' : 'POST', body); orderDialog.value = false; await load(); ElMessage.success('完整生产草稿/安排已保存')
  })
}
async function removeProduction(order: Order) {
  try { await ElMessageBox.confirm('删除该完整草稿批次及所有必要半成品任务？', '删除草稿批次') } catch { return }
  await run(async () => { await props.api(`production-orders/${order.id}/`, 'DELETE'); await load() })
}
async function changeState(order: Order, operation: string) { await run(async () => { await props.api(`production-orders/${order.id}/${operation}/`, 'POST', { revision: order.revision }); await load() }) }
function showDetail(order: Order) { detail.value = order; detailDialog.value = true }
function openReport(order: Order) { reportOrder.value = order; reportForm.value = { quantity: (Number(order.quantity) - Number(order.completed_qty)).toFixed(6), request_key: crypto.randomUUID(), batch_code: `LOT-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}`, notes: '' }; reportDialog.value = true; error.value = '' }
function openDemoProgress(order: Order) { demoOrder.value = order; demoPercent.value = order.display_progress_percent; demoDialog.value = true; error.value = '' }
async function saveDemoProgress(clear = false) {
  await run(async () => {
    await props.api(`production-orders/${demoOrder.value!.id}/demo-progress/`, 'POST', { percent: clear ? null : demoPercent.value.toFixed(2), revision: demoOrder.value!.revision })
    demoDialog.value = false; await load(); ElMessage.success(clear ? '已恢复真实报工进度' : '演示进度已同步到关联销售单')
  })
}
async function submitReport() { await run(async () => { await props.api(`production-orders/${reportOrder.value!.id}/report/`, 'POST', reportForm.value); reportDialog.value = false; await load(); ElMessage.success('报工已按冻结来源优先级回分') }) }
async function reverse(entry: Report) {
  try { await ElMessageBox.confirm('冲销最新报告并按原回分扣减；下游已开工时会拒绝。', '冲销报工', { type: 'warning' }) } catch { return }
  await run(async () => { await props.api(`reports/${entry.id}/reverse/`, 'POST', { request_key: crypto.randomUUID() }); await load() })
}
async function previewCancel(order: Order) { await run(async () => { cancelOrder.value = order; preview.value = await props.api(`production-orders/${order.id}/cancel-preview/`); cancelDialog.value = true }) }
async function confirmCancel() { await run(async () => { await props.api(`production-orders/${cancelOrder.value!.id}/cancel/`, 'POST', { token: preview.value!.token }); cancelDialog.value = false; await load(); ElMessage.success('已取消完整关联组件，相关销售恢复待排') }) }
function moveQueue(index: number, delta: number) { const target = index + delta; if (target < 0 || target >= queueOrder.value.length) return; const copy = [...queueOrder.value]; [copy[index], copy[target]] = [copy[target]!, copy[index]!]; queueOrder.value = copy }
async function saveQueue() { await run(async () => { await props.api('production-orders/queue/', 'POST', { token: queue.value!.token, order_ids: queueOrder.value, reason: queueReason.value }); queueDialog.value = false; await load() }) }
let timer: ReturnType<typeof setInterval> | undefined
let refreshing = false
async function onVisible() {
  if (document.visibilityState === 'visible' && !busy.value && !refreshing) {
    refreshing = true
    try { await load() } catch { online.value = false } finally { refreshing = false }
  }
}
onMounted(async () => {
  await run(load)
  document.addEventListener('visibilitychange', onVisible)
  timer = setInterval(async () => { if (busy.value || refreshing) return; refreshing = true; try { await load() } catch { online.value = false } finally { refreshing = false } }, 2000)
})
onUnmounted(() => { if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) })
</script>

<template>
  <div class="operations">
    <div class="sync"><span :class="online ? 'live' : 'offline'">{{ online ? '● 已连接 · 每 2 秒同步' : '● 连接中断 · 正在展示旧数据' }}</span><span>最近同步 {{ updatedAt || '—' }}</span><el-button size="small" @click="run(load)">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-tabs v-model="tab"><el-tab-pane label="跨订单排产" name="planning" /><el-tab-pane label="生产安排与报工" name="production" /><el-tab-pane label="销售进度" name="progress" /><el-tab-pane label="操作记录" name="audit" /></el-tabs>
    <template v-if="tab === 'planning'">
      <h2>生成跨订单生产建议</h2><p class="note">按来源逐级展开、合并兼容产品与共享半成品。P1 最高，依赖优先；不计算设备产能。</p>
      <div class="planner-input"><el-input v-model="planName" placeholder="方案名称" /><el-select v-model="selectedSales" multiple filterable placeholder="选择已确认且有待排量的销售单"><el-option v-for="sale in eligibleSales" :key="sale.id" :value="sale.id" :label="`${sale.number} · ${sale.customer_name} · P${sale.priority}`" /></el-select><el-button type="primary" :loading="busy" :disabled="!selectedSales.length" @click="generate">生成建议</el-button></div>
      <el-table :data="plans" stripe><el-table-column prop="name" label="方案" /><el-table-column label="状态"><template #default="{ row }">{{ statusNames[row.status] }}</template></el-table-column><el-table-column label="任务数"><template #default="{ row }">{{ row.data.tasks.length }}</template></el-table-column><el-table-column label="操作"><template #default="{ row }"><el-button link type="primary" @click="showPlan(row)">查看 / 调整</el-button><el-button v-if="row.status === 'DRAFT'" link type="primary" @click="publish(row)">发布</el-button><el-button v-if="row.status === 'DRAFT'" link type="danger" @click="removePlan(row)">删除</el-button></template></el-table-column></el-table>
    </template>
    <template v-else-if="tab === 'production'">
      <div class="toolbar"><h2>生产安排</h2><div><el-button @click="showQueue">全局插入 / 调序建议</el-button><el-button type="primary" @click="openProduction()">新增独立生产单</el-button></div></div>
      <el-table :data="orders" stripe><el-table-column prop="sequence" label="顺序" width="65" /><el-table-column prop="number" label="生产单号" min-width="180" /><el-table-column prop="item_name" label="产品" min-width="150" /><el-table-column label="等级" width="65"><template #default="{ row }">P{{ row.priority }}</template></el-table-column><el-table-column label="进度 / 真实报工" min-width="190"><template #default="{ row }">{{ row.completed_qty }} / {{ row.quantity }} {{ row.unit }}<el-progress :percentage="row.display_progress_percent" /><el-tag v-if="row.demo_progress_percent !== null" type="warning" size="small">演示进度 · 真实 {{ row.actual_progress_percent }}%</el-tag></template></el-table-column><el-table-column label="状态 / 依赖" min-width="110"><template #default="{ row }"><el-tag>{{ statusNames[row.status] }}</el-tag><div v-if="row.dependencies.some((entry: { status: string }) => entry.status !== 'COMPLETED')" class="note">前置未完成</div></template></el-table-column><el-table-column prop="line_label" label="产线" /><el-table-column label="操作" width="340"><template #default="{ row }">
        <el-button link type="primary" @click="showDetail(row)">详情</el-button><el-button v-if="!['CANCELLED', 'COMPLETED'].includes(row.status)" link type="primary" @click="openProduction(row)">编辑</el-button><el-button v-if="row.status === 'DRAFT'" link type="primary" @click="publish(plans.find(plan => plan.id === row.plan_id)!)">发布批次</el-button><el-button v-if="row.status === 'DRAFT'" link type="danger" @click="removeProduction(row)">删除批次</el-button>
        <el-button v-if="row.status === 'RELEASED'" link type="success" @click="changeState(row, 'start')">开工</el-button><el-button v-if="row.status === 'IN_PROGRESS'" link type="primary" @click="openReport(row)">报工</el-button><el-button v-if="row.status === 'IN_PROGRESS'" link @click="changeState(row, 'pause')">暂停</el-button><el-button v-if="row.status === 'PAUSED'" link type="success" @click="changeState(row, 'resume')">恢复</el-button><el-button v-if="row.status === 'RELEASED'" link type="warning" @click="previewCancel(row)">取消预览</el-button>
        <el-button v-if="['RELEASED', 'IN_PROGRESS', 'PAUSED'].includes(row.status)" link type="warning" @click="openDemoProgress(row)">调演示进度</el-button>
      </template></el-table-column></el-table>
    </template>
    <template v-else-if="tab === 'progress'">
      <div class="stats"><div><strong>{{ stats.pending }}</strong>待开工任务</div><div><strong>{{ stats.running }}</strong>生产中任务</div><div><strong>{{ stats.completed }}</strong>完成任务</div><div><strong>{{ stats.salesCompleted }}</strong>完成销售单</div></div>
      <h2>销售订单生产进度</h2><el-table :data="sales" stripe><el-table-column prop="number" label="销售单" /><el-table-column prop="customer_name" label="客户" /><el-table-column label="当前等级"><template #default="{ row }">P{{ row.priority }}</template></el-table-column><el-table-column label="状态"><template #default="{ row }">{{ statusNames[row.status] }}</template></el-table-column><el-table-column label="按销售产品回分的进度" min-width="450"><template #default="{ row }"><div v-for="line in row.progress" :key="line.line_id" class="sale-progress"><div>{{ itemName(line.item) }} · 真实报工 {{ line.fulfilled }} / {{ line.quantity }} · 待排 {{ line.remaining }}</div><el-progress :percentage="line.display_percent" /><el-tag v-if="line.demo_active" type="warning" size="small">含演示进度 · 真实 {{ Number(line.percent.toFixed(2)) }}%</el-tag></div></template></el-table-column></el-table><p class="note">仅直接销售分配计入完成量。演示值只用于展示，100%与完成状态由真实报工确认。生产单使用发布时冻结等级。</p>
    </template>
    <template v-else><h2>操作与调整记录</h2><el-table :data="audits" stripe><el-table-column prop="created_at" label="时间" /><el-table-column prop="operation" label="操作" /><el-table-column prop="object_id" label="对象" /><el-table-column label="变更"><template #default="{ row }">{{ JSON.stringify(row.details) }}</template></el-table-column></el-table></template>

    <el-dialog v-model="planDialog" :title="selectedPlan?.name" width="95%" :close-on-click-modal="false">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template v-if="selectedPlan">
        <el-collapse v-if="selectedPlan.status === 'DRAFT' && selectedPlan.inputs.order_ids"><el-collapse-item title="按销售来源调整本次安排量（0 表示排除，重算后需复核顺序）"><div v-for="sale in sales.filter(sale => selectedPlan!.inputs.order_ids?.includes(sale.id))" :key="sale.id"><h4>{{ sale.number }} · P{{ sale.priority }}</h4><div v-for="line in sale.progress" :key="line.line_id" class="source-amount"><span>{{ itemName(line.item) }} · 当前待排 {{ line.remaining }}</span><el-input v-model="amounts[String(line.line_id)]" /></div></div><el-button :loading="busy" @click="savePlan(true)">按来源量重新生成</el-button></el-collapse-item></el-collapse>
        <el-table :data="adjustments" stripe><el-table-column label="顺序" width="100"><template #default="{ row }"><el-input-number v-if="selectedPlan.status === 'DRAFT'" v-model="row.sequence" :min="1" :controls="false" /><span v-else>{{ row.sequence }}</span></template></el-table-column><el-table-column label="产品 / 合并量"><template #default="{ row }">{{ row.item.name }}<br>{{ row.quantity }} {{ row.item.unit }} · P{{ row.priority }}<el-button link type="primary" @click="selectedTask = row; sourceDialog = true">来源分配</el-button></template></el-table-column><el-table-column label="目标开始 / 完成" width="210"><template #default="{ row }"><el-date-picker v-model="row.target_start" value-format="YYYY-MM-DD" :disabled="selectedPlan.status !== 'DRAFT'" placeholder="开始" /><el-date-picker v-model="row.target_end" value-format="YYYY-MM-DD" :disabled="selectedPlan.status !== 'DRAFT'" placeholder="完成" /></template></el-table-column><el-table-column label="产线"><template #default="{ row }"><el-input v-model="row.line_label" :disabled="selectedPlan.status !== 'DRAFT'" /></template></el-table-column><el-table-column label="调序原因 / 备注"><template #default="{ row }"><el-input v-model="row.adjustment_reason" placeholder="覆盖建议顺序时必填" :disabled="selectedPlan.status !== 'DRAFT'" /><el-input v-model="row.notes" placeholder="备注" :disabled="selectedPlan.status !== 'DRAFT'" /></template></el-table-column></el-table>
        <h3>制造依赖</h3><p v-for="[child, parent] in selectedPlan.data.dependencies" :key="child + parent">{{ selectedPlan.data.tasks.find(task => task.key === child)?.item.name }} → {{ selectedPlan.data.tasks.find(task => task.key === parent)?.item.name }}</p>
        <h3>原料与包装需求</h3><el-table :data="materialSummary"><el-table-column label="原料"><template #default="{ row }">{{ row.item.name }}</template></el-table-column><el-table-column label="数量"><template #default="{ row }">{{ row.quantity.toFixed(6) }} {{ row.item.unit }}</template></el-table-column></el-table>
      </template>
      <template #footer><el-button @click="planDialog = false">关闭</el-button><el-button v-if="selectedPlan?.status === 'DRAFT'" type="primary" :loading="busy" @click="savePlan()">保存调整</el-button><el-button v-if="selectedPlan?.status === 'DRAFT'" type="success" :loading="busy" @click="publish(selectedPlan!)">发布已保存方案</el-button></template>
    </el-dialog>
    <el-dialog v-model="sourceDialog" :title="`${selectedTask?.item.name} · 来源分配`" width="800px"><el-table :data="selectedPlan?.data.allocations.filter(entry => entry.task === selectedTask?.key)"><el-table-column label="来源"><template #default="{ row }">{{ row.source.number }}</template></el-table-column><el-table-column label="用途"><template #default="{ row }">{{ kindNames[row.kind] }}</template></el-table-column><el-table-column prop="quantity" label="计划量" /><el-table-column prop="priority" label="冻结等级" /><el-table-column prop="rank" label="回分顺序" /><el-table-column prop="path" label="BOM 路径" show-overflow-tooltip /></el-table></el-dialog>
    <el-dialog v-model="orderDialog" :title="editingOrder ? '编辑生产安排' : '新增独立生产草稿'" width="650px" :close-on-click-modal="false"><el-alert v-if="error" :title="error" type="error" :closable="false" /><el-form label-width="100px"><el-form-item label="产品"><el-select v-model="productionForm.item" filterable :disabled="!!editingOrder"><el-option v-for="item in sellable" :key="item.id" :value="item.id" :label="itemName(item.id)" /></el-select></el-form-item><el-form-item label="计划量"><el-input v-model="productionForm.quantity" :disabled="!!editingOrder && editingOrder.status !== 'DRAFT'" /></el-form-item><el-form-item label="优先级"><el-select v-model="productionForm.priority" :disabled="!!editingOrder && editingOrder.status !== 'DRAFT'"><el-option v-for="p in 5" :key="p" :value="p" :label="`P${p}`" /></el-select></el-form-item><el-form-item label="目标开始"><el-date-picker v-model="productionForm.target_start" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="目标结束"><el-date-picker v-model="productionForm.target_end" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="产线"><el-input v-model="productionForm.line_label" /></el-form-item><el-form-item label="备注"><el-input v-model="productionForm.notes" type="textarea" /></el-form-item></el-form><p class="note">独立需求会生成完整必要半成品批次，不抵扣销售。修改根产品数量会重建整个草稿批次。</p><template #footer><el-button @click="orderDialog = false">关闭</el-button><el-button type="primary" :loading="busy" @click="saveProduction">保存</el-button></template></el-dialog>
    <el-dialog v-model="demoDialog" title="调整演示进度" width="600px" :close-on-click-modal="false"><el-alert v-if="error" :title="error" type="error" :closable="false" /><p>{{ demoOrder?.number }} · {{ demoOrder?.item_name }}</p><el-form label-width="110px"><el-form-item label="演示进度(%)"><el-input-number v-model="demoPercent" :min="demoOrder?.actual_progress_percent ?? 0" :max="99.99" :precision="2" :step="5" /></el-form-item></el-form><p>可设至 99.99%，不能低于真实报工进度（当前 {{ demoOrder?.actual_progress_percent }}%）。按冻结来源优先级同步到销售单。</p><p class="note">演示值不增加真实产量、不改变状态、不解锁前置依赖。新的报工或冲销会清除演示值；只有真实报工能够完成生产单与销售单。</p><template #footer><el-button @click="demoDialog = false">关闭</el-button><el-button :loading="busy" @click="saveDemoProgress(true)">恢复真实进度</el-button><el-button type="primary" :loading="busy" @click="saveDemoProgress()">保存演示进度</el-button></template></el-dialog>
    <el-dialog v-model="reportDialog" :title="`${reportOrder?.item_name} · 合格产量报工`" width="600px" :close-on-click-modal="false"><el-alert v-if="error" :title="error" type="error" :closable="false" /><el-form label-width="100px"><el-form-item label="本次合格量"><el-input v-model="reportForm.quantity" /></el-form-item><el-form-item label="生产批次"><el-input v-model="reportForm.batch_code" /></el-form-item><el-form-item label="备注"><el-input v-model="reportForm.notes" /></el-form-item></el-form><p>累计不能超过计划；按发布时来源等级依次回分。提交新的报工会清除该单演示进度。</p><template #footer><el-button @click="reportDialog = false">关闭</el-button><el-button type="primary" :loading="busy" @click="submitReport">提交报工</el-button></template></el-dialog>
    <el-dialog v-model="detailDialog" :title="detail?.number" width="95%"><template v-if="detail"><el-alert v-if="error" :title="error" type="error" :closable="false" /><h3>{{ detail.item_name }} · {{ statusNames[detail.status] }} · {{ detail.completed_qty }} / {{ detail.quantity }} {{ detail.unit }}</h3><p>冻结 BOM v{{ detail.snapshot.bom.version }} · {{ detail.snapshot.item.traceability_info }}</p><h4>前置任务</h4><p v-for="dependency in detail.dependencies" :key="dependency.id">{{ dependency.number }} · {{ statusNames[dependency.status] }}</p><h4>来源与回分（等级在发布时冻结）</h4><el-table :data="detail.allocations"><el-table-column label="来源"><template #default="{ row }">{{ row.source.number }}</template></el-table-column><el-table-column label="用途"><template #default="{ row }">{{ kindNames[row.kind] }}</template></el-table-column><el-table-column prop="priority" label="等级" /><el-table-column prop="rank" label="回分顺序" /><el-table-column prop="quantity" label="计划" /><el-table-column prop="fulfilled_qty" label="完成" /></el-table><h4>冻结组件溯源</h4><el-table :data="detail.snapshot.components"><el-table-column label="组件"><template #default="{ row }">{{ row.item.name }}</template></el-table-column><el-table-column label="溯源信息"><template #default="{ row }">{{ row.item.traceability_info }}</template></el-table-column></el-table><h4>报工与冲销流水</h4><el-table :data="detail.reports"><el-table-column prop="created_at" label="时间" /><el-table-column prop="quantity" label="增量" /><el-table-column prop="batch_code" label="生产批次" /><el-table-column prop="notes" label="备注" /><el-table-column label="回分"><template #default="{ row }">{{ row.distributions.map((entry: { allocation: number; quantity: string }) => `#${entry.allocation}: ${entry.quantity}`).join('；') }}</template></el-table-column><el-table-column label="操作"><template #default="{ row }"><el-button v-if="Number(row.quantity) > 0 && !row.reversed" link type="warning" @click="reverse(row)">冲销</el-button><span v-else>{{ row.reversed ? '已冲销' : '冲销记录' }}</span></template></el-table-column></el-table></template></el-dialog>
    <el-dialog v-model="cancelDialog" title="共享生产取消影响预览" width="750px"><el-alert v-if="error" :title="error" type="error" :closable="false" /><template v-if="preview"><el-alert :title="preview.allowed ? '以下完整关联组件将一起取消，其他销售单保持有效并恢复待排。' : '关联组件存在开工历史，禁止取消。'" :type="preview.allowed ? 'warning' : 'error'" :closable="false" /><h4>关联生产任务</h4><p v-for="task in preview.tasks" :key="task.id">{{ task.number }} · {{ statusNames[task.status] }}</p><h4>受影响销售与释放量</h4><p v-for="(sale, index) in preview.affected_sales" :key="index">{{ sale.number }} · {{ sale.quantity }}</p></template><template #footer><el-button @click="cancelDialog = false">关闭</el-button><el-button type="danger" :disabled="!preview?.allowed" :loading="busy" @click="confirmCancel">确认取消完整组件</el-button></template></el-dialog>
    <el-dialog v-model="queueDialog" title="全局未开工队列 · 插入建议" width="850px"><el-alert v-if="error" :title="error" type="error" :closable="false" /><p>新需求另建批次；下列建议仅涉及待开工单，需确认后才改变最终顺序。已开工任务保持原安排。</p><div v-if="queue" class="queue-comparison"><div><h4>当前顺序</h4><p v-for="id in queue.current" :key="id">{{ queue.orders.find(order => order.id === id)?.item_name }} · P{{ queue.orders.find(order => order.id === id)?.priority }}</p></div><div><h4>建议 / 人工调整顺序</h4><div v-for="(id, index) in queueOrder" :key="id" class="queue-row"><span>{{ index + 1 }}. {{ queue.orders.find(order => order.id === id)?.item_name }} · P{{ queue.orders.find(order => order.id === id)?.priority }}</span><el-button size="small" @click="moveQueue(index, -1)">上移</el-button><el-button size="small" @click="moveQueue(index, 1)">下移</el-button></div></div></div><el-input v-model="queueReason" placeholder="调整原因（必填）" /><template #footer><el-button @click="queueDialog = false">暂不调整</el-button><el-button type="primary" :loading="busy" @click="saveQueue">确认最终顺序</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.sync { display: flex; gap: 20px; align-items: center; justify-content: flex-end; font-size: 13px; color: #66758a; }.live { color: #22865b; }.offline { color: #bd4c33; }
.planner-input { display: flex; gap: 12px; margin: 24px 0; }.planner-input > .el-input { flex: 1; }.planner-input > .el-select { flex: 3; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 24px 0; }.stats > div { background: #f4f7fb; border-radius: 10px; padding: 20px; }.stats strong { display: block; font-size: 30px; color: #3270ca; }
.sale-progress { padding: 10px 0; }.source-amount { display: flex; gap: 20px; margin: 12px; align-items: center; }.source-amount .el-input { width: 160px; }.queue-comparison { display: grid; grid-template-columns: 1fr 2fr; gap: 24px; }.queue-row { display: flex; gap: 8px; align-items: center; margin: 10px 0; }.queue-row span { flex: 1; }
:deep(.el-date-editor.el-input) { width: 180px; }:deep(.el-input-number) { width: 80px; }
:deep(.el-input-number:not(.is-without-controls)) { width: 200px; }
</style>
