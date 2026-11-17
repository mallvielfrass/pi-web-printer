<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import FileList from '../components/FileList.vue'
import FilePreview from '../components/FilePreview.vue'

const route = useRoute()
const files = ref([])
const selected = ref(null)
const printer = ref(null)
const error = ref('')
const busy = ref(false)
const started = ref(false)
const job = ref(null)
let jobTimer = 0

// Полоска отмечает стадию, не процент страниц: CUPS его не присылает.
const jobStages = [
  { id: 'sent', label: 'Отправлено' },
  { id: 'queued', label: 'В очереди' },
  { id: 'printing', label: 'Печатает' },
  { id: 'done', label: 'Готово' },
]
const jobLabels = {
  sent: 'Отправлено',
  queued: 'В очереди',
  printing: 'Печатает',
  stopped: 'Остановлено',
  done: 'Готово',
  canceled: 'Отменено',
  aborted: 'Сбой печати',
  unknown: 'Нет данных о задании',
}
const input = ref(null)
const picked = ref('')
const copies = ref(1)
const pages = ref('')
const media = ref('A4')
const sides = ref('none')
const fit = ref(true)
const sheet = ref(null)

const accept = '.pdf,.jpg,.jpeg,.png,.tif,.tiff,.txt,.doc,.docx,.odt,.xls,.xlsx,.ppt,.pptx'

async function reload() {
  const data = await api.files({ limit: 30, sort: 'created_at', order: 'desc' })
  files.value = data.files
}

function pickMedia(status) {
  const sizes = status?.media || []
  const labels = status?.media_labels || {}
  const preferred = status?.media_default
  if (preferred && sizes.includes(preferred)) return preferred
  const a4 = sizes.find((size) => size === 'A4' || labels[size] === 'A4')
  if (a4) return a4
  return sizes[0] || 'A4'
}

async function loadPrinter() {
  printer.value = await api.printer()
  media.value = pickMedia(printer.value)
  if (!printer.value.duplex) sides.value = 'none'
}

async function selectById(id) {
  if (!id) return
  const found = files.value.find((item) => String(item.id) === String(id))
  if (found) {
    selected.value = found
    return
  }
  const data = await api.file(id)
  selected.value = data.file
}

function jobText(stage) {
  return jobLabels[stage] || jobLabels.unknown
}

function jobStep(stage) {
  if (stage === 'done') return 3
  if (stage === 'printing' || stage === 'stopped') return 2
  if (stage === 'queued') return 1
  return 0
}

function jobFill(stage) {
  return ['25%', '50%', '75%', '100%'][jobStep(stage)]
}

function jobBad(stage) {
  return stage === 'stopped' || stage === 'canceled' || stage === 'aborted' || stage === 'unknown'
}

function jobReached(index, stage) {
  if (stage === 'canceled' || stage === 'aborted' || stage === 'unknown') return false
  return index <= jobStep(stage)
}

function stopJobWatch() {
  if (jobTimer) {
    clearInterval(jobTimer)
    jobTimer = 0
  }
}

async function refreshJob() {
  if (!job.value) return
  try {
    const data = await api.job(job.value.id)
    job.value = data
    if (data.stage === 'done' || data.stage === 'canceled' || data.stage === 'aborted' || data.stage === 'unknown') {
      stopJobWatch()
    }
  } catch {
    job.value = { id: job.value.id, stage: 'unknown' }
    stopJobWatch()
  }
}

function watchJob(id) {
  stopJobWatch()
  job.value = { id, stage: 'sent' }
  // Стадия в CUPS меняется секундами, чаще опрашивать плату незачем.
  jobTimer = setInterval(refreshJob, 2000)
  refreshJob()
}

async function upload() {
  const chosen = input.value?.files?.[0]
  if (!chosen) {
    error.value = 'Выберите файл'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const data = await api.upload(chosen)
    input.value.value = ''
    picked.value = ''
    await reload()
    select(data.file)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function select(file) {
  selected.value = file
  error.value = ''
  await nextTick()
  sheet.value?.scrollIntoView({ block: 'start' })
}

async function doPrint() {
  if (!selected.value) return
  busy.value = true
  error.value = ''
  try {
    const data = await api.print({
      file_id: selected.value.id,
      copies: copies.value,
      pages: pages.value,
      sides: printer.value?.duplex ? sides.value : 'none',
      media: media.value,
      fit: fit.value,
    })
    started.value = true
    if (data && data.job_id) watchJob(data.job_id)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function onDeleted(file) {
  if (selected.value?.id === file.id) selected.value = null
  await reload()
}

onMounted(async () => {
  try {
    await Promise.all([reload(), loadPrinter()])
    if (route.query.file) await selectById(route.query.file)
  } catch (err) {
    error.value = err.message
  }
})

watch(() => route.query.file, async (id) => {
  if (!id) return
  try {
    await selectById(id)
  } catch (err) {
    error.value = err.message
  }
})

onBeforeUnmount(stopJobWatch)
</script>

<template>
  <h1>Печать</h1>
  <form class="stack" @submit.prevent="upload">
    <label class="file-pick">
      <span>{{ picked || 'Выбрать файл' }}</span>
      <input ref="input" type="file" :accept="accept" @change="picked = $event.target.files?.[0]?.name || ''">
    </label>
    <button type="submit" class="primary" :disabled="busy">Загрузить</button>
  </form>

  <p v-if="error" class="msg error">{{ error }}</p>

  <section v-if="job" class="panel job">
    <p class="job-title">{{ jobText(job.stage) }}</p>
    <div
      class="bar"
      :class="{ bad: jobBad(job.stage) }"
      role="progressbar"
      :aria-valuetext="jobText(job.stage)"
      aria-valuemin="1"
      aria-valuemax="4"
      :aria-valuenow="jobStep(job.stage) + 1"
    >
      <span :style="{ width: jobFill(job.stage) }"></span>
    </div>
    <ol class="stages">
      <li v-for="(item, index) in jobStages" :key="item.id" :class="{ on: jobReached(index, job.stage) }">
        {{ item.label }}
      </li>
    </ol>
  </section>

  <section v-if="selected" ref="sheet" class="panel sheet">
    <h2>{{ selected.name }}</h2>
    <FilePreview :file="selected" />
    <form class="stack" @submit.prevent="doPrint">
      <label>
        Копии
        <input v-model.number="copies" type="number" min="1" max="99" inputmode="numeric">
      </label>
      <label>
        Страницы
        <input v-model="pages" type="text" placeholder="все" inputmode="numeric" autocapitalize="off">
      </label>
      <label>
        Бумага
        <select v-model="media">
          <option v-for="size in (printer?.media || ['A4'])" :key="size" :value="size">
            {{ printer?.media_labels?.[size] || size }}
          </option>
        </select>
      </label>
      <label v-if="printer?.duplex">
        Стороны
        <select v-model="sides">
          <option value="none">Односторонняя</option>
          <option value="long">Двусторонняя, длинный край</option>
          <option value="short">Двусторонняя, короткий край</option>
        </select>
      </label>
      <label class="check">
        <input v-model="fit" type="checkbox">
        Вместить в страницу
      </label>
      <button type="submit" class="primary" :disabled="busy">Печать</button>
    </form>
  </section>

  <h2>Недавние файлы</h2>
  <FileList
    :files="files"
    :selected-id="selected?.id ?? null"
    @open="select"
    @deleted="onDeleted"
    @error="error = $event"
  />

  <div v-if="started" class="modal-back" @click.self="started = false">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="print-started" @keydown.esc="started = false">
      <h2 id="print-started">Печать запущена</h2>
      <button type="button" class="primary" autofocus @click="started = false">Хорошо</button>
    </div>
  </div>
</template>
