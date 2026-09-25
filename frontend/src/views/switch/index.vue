<template>
  <section class="page" data-module="switch">
    <header class="page-head">
      <div>
        <h2>转辙机管理</h2>
        <p class="page-desc">按设备型号与所属区段的统一阈值口径，对动作电流分级：超过上限列为待处理，同一区段多台超限时按安装道岔排列风险序。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记转辙机</button>
        <button class="btn" type="button" @click="openThresholds">阈值口径</button>
        <button class="btn" type="button" @click="exportRows">导出转辙机清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="filters.keyword" placeholder="按设备编号检索" />
      </label>
      <label class="filter-item">
        <span>所属区段</span>
        <input v-model="filters.section" placeholder="按所属区段检索" />
      </label>
      <label class="filter-item">
        <span>分级</span>
        <select v-model="filters.level">
          <option value="">全部分级</option>
          <option v-for="level in levels" :key="level" :value="level">{{ level }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>设备状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>风险序</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定依据</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-over': row.level === '超限待处理', 'row-unknown': row.level === '待判定' }">
          <td>
            <strong v-if="row.风险序" class="risk-no">{{ row.风险序 }}</strong>
            <span v-else>—</span>
          </td>
          <td v-for="column in columns" :key="column">
            <template v-if="column === '设备型号'">
              <span>{{ row[column] || '' }}</span>
              <span v-if="!String(row[column] || '').trim()" class="warn-mark">缺型号</span>
            </template>
            <template v-else-if="column === '分级'">
              <span class="level-badge" :class="levelClass(String(row.level))">{{ row.level ?? '—' }}</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="reason-cell">{{ row.判定依据 ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openMeasure(row)">录入电流</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无转辙机数据，可先登记转辙机</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条转辙机记录（超限设备置顶，同一区段按安装道岔排列）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记转辙机 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <h3>登记转辙机</h3>
        <p class="page-desc">动作电流只能落在该型号允许保存范围内，阈值范围之外的输入不允许保存。</p>
        <label v-for="field in createFields" :key="field.prop" class="form-item">
          <span>{{ field.label }}{{ field.required ? ' *' : '' }}</span>
          <select v-if="field.prop === '设备型号'" v-model="createForm[field.prop]">
            <option value="" disabled>请选择设备型号</option>
            <option v-for="model in modelOptions" :key="model" :value="model">{{ model }}</option>
          </select>
          <input v-else v-model="createForm[field.prop]" :placeholder="field.hint ?? ''" />
        </label>
        <p v-if="createHint" class="hint-text">{{ createHint }}</p>
        <p v-if="createError" class="error-text">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">保存</button>
        </div>
      </div>
    </div>

    <!-- 录入动作电流 / 转换时间 -->
    <div v-if="measureTarget" class="modal-mask" @click.self="measureTarget = null">
      <div class="modal">
        <h3>录入动作电流 — {{ measureTarget.设备编号 }}</h3>
        <p class="page-desc">
          {{ measureTarget.设备型号 || '（型号未填）' }} / {{ measureTarget.所属区段 || '未分区段' }}：
          <template v-if="measureRule">允许保存 {{ measureRule['电流下限'] }}~{{ measureRule['电流最大可录'] }}A，
          关注线 {{ measureRule['关注线'] }}A，上限 {{ measureRule['电流上限'] }}A；
          转换时间上限 {{ measureRule['转换时间上限'] }}s</template>
          <template v-else>该型号暂无阈值口径，需先在「阈值口径」中登记</template>
        </p>
        <label class="form-item">
          <span>动作电流（A）</span>
          <input v-model="measureForm.动作电流" placeholder="如 2.4" />
        </label>
        <label class="form-item">
          <span>转换时间（s）</span>
          <input v-model="measureForm.转换时间" placeholder="如 4.2" />
        </label>
        <p v-if="measureError" class="error-text">{{ measureError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="measureTarget = null">取消</button>
          <button class="btn primary" type="button" @click="submitMeasure">保存</button>
        </div>
      </div>
    </div>

    <!-- 阈值口径 -->
    <div v-if="showThresholds" class="modal-mask modal-wide" @click.self="showThresholds = false">
      <div class="modal">
        <h3>动作电流阈值口径</h3>
        <p class="page-desc">按设备型号设默认阈值，可再按所属区段收紧；超限电流不允许保存，超过上限标为待处理。</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>所属区段</th><th>设备型号</th><th>口径来源</th><th>电流下限(A)</th>
              <th>关注线(A)</th><th>电流上限(A)</th><th>最大可录(A)</th>
              <th>转换时间上限(s)</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in thresholdRows" :key="`${rule.所属区段}|${rule.设备型号}`">
              <td>{{ rule.所属区段 || '（型号默认）' }}</td>
              <td>{{ rule.设备型号 }}</td>
              <td>{{ rule.阈值来源 }}</td>
              <td>{{ rule['电流下限'] }}</td>
              <td>{{ rule['关注线'] }}</td>
              <td>{{ rule['电流上限'] }}</td>
              <td>{{ rule['电流最大可录'] }}</td>
              <td>{{ rule['转换时间上限'] }}</td>
              <td>
                <button v-if="rule.所属区段" class="link danger" type="button" @click="removeThreshold(rule)">删除覆盖</button>
              </td>
            </tr>
          </tbody>
        </table>

        <h4 class="form-title">新增/收紧区段口径</h4>
        <div class="form-grid">
          <label class="form-item">
            <span>设备型号 *</span>
            <select v-model="thresholdForm.设备型号">
              <option value="" disabled>请选择型号</option>
              <option v-for="model in modelOptions" :key="model" :value="model">{{ model }}</option>
            </select>
          </label>
          <label class="form-item">
            <span>所属区段 *</span>
            <input v-model="thresholdForm.所属区段" placeholder="如 上行咽喉" />
          </label>
          <label class="form-item">
            <span>关注线（A）</span>
            <input v-model="thresholdForm.current_warn" placeholder="留空沿用默认" />
          </label>
          <label class="form-item">
            <span>电流上限（A）</span>
            <input v-model="thresholdForm.current_upper" placeholder="留空沿用默认" />
          </label>
          <label class="form-item">
            <span>转换时间上限（s）</span>
            <input v-model="thresholdForm.switch_time_upper" placeholder="留空沿用默认" />
          </label>
        </div>
        <p v-if="thresholdError" class="error-text">{{ thresholdError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="showThresholds = false">关闭</button>
          <button class="btn primary" type="button" @click="submitThreshold">保存口径</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ThresholdRule = Record<string, string | number>

const ENDPOINT = '/api/switch'
const columns = ['设备编号', '设备型号', '安装道岔', '动作电流', '转换时间', '所属区段', '上次检修日', '分级', '设备状态']
const actions = ['确认检修', '登记动作异常', '更换设备']
const statuses = ['待检修', '运用正常', '动作异常', '已更换']
const levels = ['正常', '关注', '超限待处理', '动作异常', '待判定', '已更换']

const createFields = [
  { prop: '设备编号', label: '设备编号', required: true },
  { prop: '设备型号', label: '设备型号', required: true },
  { prop: '安装道岔', label: '安装道岔', required: true, hint: '如 3# 或 1/3#' },
  { prop: '所属区段', label: '所属区段' },
  { prop: '动作电流', label: '动作电流（A）', hint: '需在型号允许范围内' },
  { prop: '转换时间', label: '转换时间（s）' },
  { prop: '上次检修日', label: '上次检修日', hint: 'YYYY-MM-DD' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const summary = ref<Record<string, number>>({})
const errorMessage = ref('')
const filters = reactive<Record<string, string>>({ keyword: '', status: '', level: '', section: '' })

const thresholdRows = ref<ThresholdRule[]>([])
const modelOptions = ref<string[]>([])

const showCreate = ref(false)
const createForm = reactive<Record<string, string>>(Object.fromEntries(createFields.map((f) => [f.prop, ''])))
const createError = ref('')
const createHint = computed(() => {
  const rule = modelOptions.value.length && createForm.设备型号
    ? thresholdRows.value.find((item) => item.设备型号 === createForm.设备型号 && !item.所属区段)
    : undefined
  if (!rule) return ''
  return `该型号允许保存范围 ${rule['电流下限']}~${rule['电流最大可录']}A，关注线 ${rule['关注线']}A，上限 ${rule['电流上限']}A`
})

const showThresholds = ref(false)
const thresholdForm = reactive<Record<string, string>>({
  设备型号: '', 所属区段: '', current_warn: '', current_upper: '', switch_time_upper: '',
})
const thresholdError = ref('')

const measureTarget = ref<Row | null>(null)
const measureForm = reactive({ 动作电流: '', 转换时间: '' })
const measureError = ref('')

const statCards = computed(() => [
  { label: '运用正常', value: summary.value['运用正常'] ?? 0, cls: '' },
  { label: '正常待检修', value: summary.value['正常待检修'] ?? 0, cls: '' },
  { label: '超限待处理', value: summary.value['超限待处理'] ?? 0, cls: 'stat-danger' },
  { label: '动作异常（不含待处理）', value: summary.value['动作异常'] ?? 0, cls: '' },
  { label: '关注', value: summary.value['关注'] ?? 0, cls: 'stat-warn' },
  { label: '待判定（需补数据）', value: summary.value['待判定'] ?? 0, cls: 'stat-warn' },
  { label: '已更换', value: summary.value['已更换'] ?? 0, cls: '' },
])

const measureRule = computed(() => {
  const target = measureTarget.value
  if (!target) return undefined
  const section = String(target.所属区段 || '')
  return thresholdRows.value.find((rule) => rule.设备型号 === target.设备型号 && rule.所属区段 === section)
    ?? thresholdRows.value.find((rule) => rule.设备型号 === target.设备型号 && !rule.所属区段)
})

function levelClass(level: string): string {
  if (level === '超限待处理') return 'level-over'
  if (level === '动作异常') return 'level-abnormal'
  if (level === '待判定') return 'level-unknown'
  if (level === '关注') return 'level-watch'
  if (level === '已更换') return 'level-replaced'
  return 'level-normal'
}

function resetFilters() {
  for (const key of Object.keys(filters)) filters[key] = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function loadThresholds() {
  try {
    const response = await request(`${ENDPOINT}/thresholds`)
    if (!response.ok) throw new Error('阈值口径读取失败')
    const payload = await response.json()
    thresholdRows.value = payload.items ?? []
    modelOptions.value = [...new Set(thresholdRows.value.map((item) => String(item.设备型号)))]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '阈值口径读取失败'
  }
}

function openCreate() {
  createFields.forEach((field) => { createForm[field.prop] = '' })
  createError.value = ''
  showCreate.value = true
}

async function submitCreate() {
  createError.value = ''
  const values = Object.fromEntries(Object.entries(createForm).filter(([, v]) => v.trim() !== ''))
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '转辙机登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '转辙机登记失败'
  }
}

async function openThresholds() {
  thresholdError.value = ''
  showThresholds.value = true
  await loadThresholds()
}

async function submitThreshold() {
  thresholdError.value = ''
  const values: Record<string, string> = { ...thresholdForm }
  if (!values.设备型号 || !values.所属区段) {
    thresholdError.value = '设备型号与所属区段都要填写'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/thresholds`, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '阈值保存失败')
    for (const key of ['所属区段', 'current_warn', 'current_upper', 'switch_time_upper']) thresholdForm[key] = ''
    await loadThresholds()
    await reload()
  } catch (error) {
    thresholdError.value = error instanceof Error ? error.message : '阈值保存失败'
  }
}

async function removeThreshold(rule: ThresholdRule) {
  const query = new URLSearchParams({ model: String(rule.设备型号), section: String(rule.所属区段) })
  try {
    const response = await request(`${ENDPOINT}/thresholds?${query.toString()}`, { method: 'DELETE' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '覆盖口径删除失败')
    await loadThresholds()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '覆盖口径删除失败'
  }
}

function openMeasure(row: Row) {
  measureTarget.value = row
  measureForm.动作电流 = String(row.动作电流 ?? '')
  measureForm.转换时间 = String(row.转换时间 ?? '')
  measureError.value = ''
}

async function submitMeasure() {
  const target = measureTarget.value
  if (!target) return
  measureError.value = ''
  const values: Record<string, string> = {}
  if (measureForm.动作电流.trim() !== '') values.动作电流 = measureForm.动作电流.trim()
  if (measureForm.转换时间.trim() !== '') values.转换时间 = measureForm.转换时间.trim()
  if (!Object.keys(values).length) {
    measureError.value = '至少填写动作电流或转换时间中的一项'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${target.id}/measurements`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '录入失败')
    measureTarget.value = null
    await reload()
  } catch (error) {
    measureError.value = error instanceof Error ? error.message : '录入失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '转辙机动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转辙机操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v.trim() !== ''),
  ).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('转辙机列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    summary.value = payload.summary ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转辙机列表读取失败'
  }
}

onMounted(() => {
  void loadThresholds()
  void reload()
})
</script>
