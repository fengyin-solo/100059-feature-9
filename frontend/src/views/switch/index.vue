<template>
  <section class="page" data-module="switch">
    <header class="page-head">
      <div>
        <h2>转辙机管理</h2>
        <p class="page-desc">
          按设备型号 + 所属区段统一动作电流与转换时间告警阈值；超上限设备判超限、列待处理，
          同一区段多台超限时按安装道岔号排风险序，号小的风险最高。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="runGrading">执行分级</button>
        <button class="btn" type="button" @click="openThresholds">阈值设置</button>
        <button class="btn" type="button" @click="exportRows">导出转辙机清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="missingModelRows.length" class="warn-banner">
      以下 {{ missingModelRows.length }} 条转辙机判定用的设备型号未填写，无法匹配阈值，已判「无法判定」：
      <span v-for="(row, index) in missingModelRows" :key="String(row.id)">
        {{ row['设备编号'] }}（{{ row['安装道岔'] || '道岔未填' }}/{{ row['所属区段'] || '区段未填' }}）<span v-if="index < missingModelRows.length - 1">、</span>
      </span>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="keyword" placeholder="按设备编号检索" />
      </label>
      <label class="filter-item">
        <span>设备状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>电流分级</span>
        <select v-model="gradeFilter">
          <option value="">全部分级</option>
          <option v-for="grade in grades" :key="grade" :value="grade">{{ grade }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="rowClass(row)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '电流分级'" :class="['grade-tag', gradeClass(row)]">{{ row[column] ?? '—' }}</span>
            <span v-else-if="column === '风险序号'">{{ row[column] ? `第 ${row[column]} 位` : '—' }}</span>
            <span v-else-if="column === '判定依据'" :title="String(row[column] ?? '')" class="basis-cell">{{ row[column] ?? '—' }}</span>
            <span v-else>{{ displayCell(row, column) }}</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
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
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的转辙机</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条转辙机记录，列表默认超限设备在前、同区段按风险序排列</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 阈值设置弹窗：班组统一口径，范围之外的输入保存不了 -->
    <div v-if="thresholdOpen" class="modal-mask" @click.self="thresholdOpen = false">
      <div class="modal-card">
        <h3>动作电流分级阈值（按设备型号 + 所属区段）</h3>
        <p class="modal-tip">
          允许保存范围：动作电流上限 0.5~5.0A，转换时间上限 1~30s；实测值超上限判超限并待处理，
          达上限 90% 判预警。
        </p>
        <table class="data-table threshold-table">
          <thead>
            <tr>
              <th>设备型号</th>
              <th>所属区段</th>
              <th>动作电流上限(A)</th>
              <th>转换时间上限(s)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in thresholds" :key="`${item['设备型号']}-${item['所属区段']}`">
              <td><input v-model="item['设备型号']" @change="touchThreshold(index)" /></td>
              <td><input v-model="item['所属区段']" @change="touchThreshold(index)" /></td>
              <td><input v-model.number="item['动作电流上限A']" @change="touchThreshold(index)" /></td>
              <td><input v-model.number="item['转换时间上限s']" @change="touchThreshold(index)" /></td>
              <td><button class="link" type="button" @click="saveThreshold(item)">保存</button></td>
            </tr>
            <tr v-for="(item, index) in blankThresholds" :key="`blank-${index}`">
              <td><input v-model="item['设备型号']" placeholder="如 ZD6" /></td>
              <td><input v-model="item['所属区段']" placeholder="如 一号线东段" /></td>
              <td><input v-model.number="item['动作电流上限A']" placeholder="0.5~5.0" /></td>
              <td><input v-model.number="item['转换时间上限s']" placeholder="1~30" /></td>
              <td><button class="link" type="button" @click="saveThreshold(item, true)">保存新增</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="thresholdMessage" :class="thresholdOk ? 'ok-text' : 'error-text'">{{ thresholdMessage }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="addBlank">新增一行</button>
          <button class="btn primary" type="button" @click="thresholdOpen = false">完成</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | string[]>
type Threshold = Record<string, string | number>

const ENDPOINT = '/api/switch'
const columns = [
  '设备编号', '设备型号', '安装道岔', '动作电流', '转换时间',
  '所属区段', '电流分级', '风险序号', '判定依据', '上次检修日', '设备状态',
]
const statuses = ['待检修', '运用正常', '动作异常', '已更换']
const grades = ['超限', '预警', '正常', '无法判定', '不参评']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const gradeFilter = ref('')

const stats = ref([
  { label: '超限待处理（台）', value: 0, cls: 'text-danger' },
  { label: '预警（台）', value: 0, cls: 'text-warn' },
  { label: '运用正常（台）', value: 0, cls: '' },
  { label: '无法判定（台）', value: 0, cls: 'text-danger' },
])

const thresholdOpen = ref(false)
const thresholds = ref<Threshold[]>([])
const blankThresholds = ref<Threshold[]>([])
const thresholdMessage = ref('')
const thresholdOk = ref(false)

const missingModelRows = computed(() =>
  rows.value.filter((row) => !String(row['设备型号'] ?? '').trim()),
)

function displayCell(row: Row, column: string): string | number | null {
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : (value as string | number)
}

function gradeClass(row: Row): string {
  switch (row['电流分级']) {
    case '超限':
      return 'grade-over'
    case '预警':
      return 'grade-warn'
    case '无法判定':
      return 'grade-unknown'
    case '不参评':
      return 'grade-skip'
    default:
      return 'grade-normal'
  }
}

function rowClass(row: Row): Record<string, boolean> {
  return {
    'row-over': row['电流分级'] === '超限',
    'row-unknown': row['电流分级'] === '无法判定',
  }
}

function availableActions(row: Row): string[] {
  const all = ['确认检修', '登记动作异常', '更换设备']
  if (row['设备状态'] === '已更换') {
    return []
  }
  // 超限时确认检修必然后端拦截，前端也不再给出入口，避免班组误操作
  if (row['电流分级'] === '超限' || row['电流分级'] === '无法判定') {
    return all.filter((action) => action !== '确认检修')
  }
  return all
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  gradeFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (gradeFilter.value) params.set('grade', gradeFilter.value)
  params.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('转辙机列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value[0].value = (payload.items ?? []).filter((x: Row) => x['电流分级'] === '超限').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转辙机列表读取失败'
  }
  // 分级统计取全量报告，避免受当前筛选条件影响
  try {
    const response = await request(`${ENDPOINT}/grading/report`)
    if (response.ok) {
      const report = await response.json()
      const summary = report.summary ?? {}
      stats.value = [
        { label: '超限待处理（台）', value: summary['超限'] ?? 0, cls: 'text-danger' },
        { label: '预警（台）', value: summary['预警'] ?? 0, cls: 'text-warn' },
        { label: '运用正常（台）', value: summary['运用正常'] ?? 0, cls: '' },
        { label: '无法判定（台）', value: summary['无法判定'] ?? 0, cls: 'text-danger' },
      ]
    }
  } catch {
    // 统计拉取失败不阻塞列表，页脚已有错误提示位
  }
}

async function runGrading() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/grading/run`, { method: 'POST', body: JSON.stringify({}) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '分级未执行成功')
    }
    errorMessage.value = ''
    await reload()
    window.alert(payload.message)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分级执行失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      // 超限拦截、口径缺失等原因直接展示后端口径，保证班组说法一致
      errorMessage.value = payload.message || '转辙机动作未生效'
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转辙机操作失败'
  }
}

async function openThresholds() {
  thresholdMessage.value = ''
  thresholdOpen.value = true
  blankThresholds.value = []
  try {
    const response = await request(`${ENDPOINT}/thresholds`)
    if (!response.ok) {
      throw new Error('阈值口径读取失败')
    }
    const payload = await response.json()
    thresholds.value = (payload.items ?? []).map((item: Threshold) => reactive(item))
  } catch (error) {
    thresholdOpen.value = false
    errorMessage.value = error instanceof Error ? error.message : '阈值口径读取失败'
  }
}

function addBlank() {
  blankThresholds.value.push(reactive({ 设备型号: '', 所属区段: '', 动作电流上限A: '', 转换时间上限s: '' }))
}

function touchThreshold(_index: number) {
  thresholdMessage.value = ''
}

function validateThreshold(item: Threshold): string {
  const current = Number(item['动作电流上限A'])
  const convertTime = Number(item['转换时间上限s'])
  if (!String(item['设备型号'] ?? '').trim()) return '设备型号不能为空'
  if (!String(item['所属区段'] ?? '').trim()) return '所属区段不能为空'
  if (!Number.isFinite(current)) return '动作电流上限必须填写数字'
  if (!Number.isFinite(convertTime)) return '转换时间上限必须填写数字'
  if (current < 0.5 || current > 5.0) return '动作电流上限只允许 0.5~5.0A，超出范围不能保存'
  if (convertTime < 1 || convertTime > 30) return '转换时间上限只允许 1~30s，超出范围不能保存'
  return ''
}

async function saveThreshold(item: Threshold, isBlank = false) {
  thresholdMessage.value = ''
  const invalid = validateThreshold(item)
  if (invalid) {
    thresholdOk.value = false
    thresholdMessage.value = invalid
    return
  }
  try {
    const response = await request(`${ENDPOINT}/thresholds`, {
      method: 'POST',
      body: JSON.stringify({ values: item }),
    })
    const payload = await response.json()
    thresholdOk.value = Boolean(payload.ok)
    thresholdMessage.value = payload.message
    if (payload.ok) {
      await openThresholds()
      if (isBlank) {
        blankThresholds.value = blankThresholds.value.filter((row) => row !== item)
      }
      await reload()
    }
  } catch (error) {
    thresholdOk.value = false
    thresholdMessage.value = error instanceof Error ? error.message : '阈值保存失败'
  }
}

onMounted(reload)
</script>
