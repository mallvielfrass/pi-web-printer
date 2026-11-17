<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import FileList from '../components/FileList.vue'
import FilePreview from '../components/FilePreview.vue'

const files = ref([])
const selected = ref(null)
const resolution = ref(300)
const mode = ref('color')
const format = ref('png')
const geometry = ref('A4')
const busy = ref(false)
const error = ref('')

async function reload() {
  const data = await api.files({ kind: 'scan', limit: 30, sort: 'created_at', order: 'desc' })
  files.value = data.files
}

async function scan() {
  busy.value = true
  error.value = ''
  try {
    const data = await api.scan({
      resolution: resolution.value,
      mode: mode.value,
      format: format.value,
      geometry: geometry.value,
    })
    await reload()
    selected.value = data.file
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
    await reload()
  } catch (err) {
    error.value = err.message
  }
})
</script>

<template>
  <h1>Сканирование</h1>
  <form class="scan-layout" @submit.prevent="scan">
    <div class="scan-fields">
      <label>
        Разрешение
        <select v-model.number="resolution">
          <option :value="75">75 dpi</option>
          <option :value="150">150 dpi</option>
          <option :value="300">300 dpi</option>
          <option :value="600">600 dpi</option>
          <option :value="1200">1200 dpi</option>
        </select>
      </label>
      <label>
        Цвет
        <select v-model="mode">
          <option value="color">Цветной</option>
          <option value="gray">Оттенки серого</option>
          <option value="lineart">Чёрно-белый</option>
        </select>
      </label>
      <label>
        Область
        <select v-model="geometry">
          <option value="A4">A4</option>
          <option value="A5">A5</option>
          <option value="B5">B5</option>
          <option value="Letter">Letter</option>
          <option value="Full size">Всё стекло</option>
        </select>
      </label>
      <label>
        Формат
        <select v-model="format">
          <option value="png">PNG</option>
          <option value="jpeg">JPEG</option>
          <option value="tiff">TIFF</option>
          <option value="pdf">PDF</option>
        </select>
      </label>
    </div>
    <button type="submit" class="scan-go" :disabled="busy">
      {{ busy ? 'Сканирование…' : 'Сканировать' }}
    </button>
  </form>
  <p class="muted hint">На Pi с 1 ГБ для текста лучше 150–300 dpi и оттенки серого. Цвет 1200 dpi для поворота сохраняйте как PDF.</p>
  <p v-if="error" class="msg error">{{ error }}</p>

  <section v-if="selected" class="panel">
    <h2>{{ selected.name }}</h2>
    <FilePreview :file="selected" />
  </section>

  <h2>Последние сканы</h2>
  <FileList
    :files="files"
    :selected-id="selected?.id ?? null"
    @open="selected = $event"
    @deleted="onDeleted"
    @error="error = $event"
  />
</template>
