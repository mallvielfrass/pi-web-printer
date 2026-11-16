<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import FileList from '../components/FileList.vue'
import FilePreview from '../components/FilePreview.vue'

const route = useRoute()
const files = ref([])
const selected = ref(null)
const printer = ref(null)
const error = ref('')
const ok = ref('')
const busy = ref(false)
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

async function loadPrinter() {
  printer.value = await api.printer()
  const sizes = printer.value.media || []
  if (sizes.length && !sizes.includes(media.value)) media.value = sizes[0]
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

async function select(file) {
  selected.value = file
  ok.value = ''
  error.value = ''
  await nextTick()
  sheet.value?.scrollIntoView({ block: 'start' })
}

async function upload() {
  const chosen = input.value?.files?.[0]
  if (!chosen) {
    error.value = 'Выберите файл'
    return
  }
  busy.value = true
  error.value = ''
  ok.value = ''
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

async function doPrint() {
  if (!selected.value) return
  busy.value = true
  error.value = ''
  ok.value = ''
  try {
    await api.print({
      file_id: selected.value.id,
      copies: copies.value,
      pages: pages.value,
      sides: printer.value?.duplex ? sides.value : 'none',
      media: media.value,
      fit: fit.value,
    })
    ok.value = 'Отправлено на печать'
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
  <p v-if="ok" class="msg ok">{{ ok }}</p>

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
          <option v-for="size in (printer?.media || ['A4'])" :key="size" :value="size">{{ size }}</option>
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
</template>
