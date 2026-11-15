<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { previewUrl } from '../api'

const props = defineProps({
  file: { type: Object, required: true },
})

const kind = ref('pdf')
const text = ref('')
const objectUrl = ref('')
const error = ref('')
const loading = ref(false)

function kindOf(name) {
  const ext = String(name).split('.').pop().toLowerCase()
  if (ext === 'png' || ext === 'jpg' || ext === 'jpeg') return 'image'
  if (ext === 'txt') return 'text'
  // TIFF и офисные файлы сервер отдаёт уже как PDF: телефон их так и покажет.
  return 'pdf'
}

function revoke() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
}

async function load(file) {
  revoke()
  error.value = ''
  text.value = ''
  kind.value = kindOf(file.name)
  loading.value = true
  try {
    const response = await fetch(previewUrl(file.id), { credentials: 'same-origin' })
    if (!response.ok) {
      let detail = 'Не удалось открыть файл'
      try {
        const data = await response.json()
        if (typeof data.detail === 'string') detail = data.detail
      } catch {
        // тело ошибки без JSON
      }
      error.value = detail
      return
    }
    if (kind.value === 'text') {
      text.value = await response.text()
      return
    }
    objectUrl.value = URL.createObjectURL(await response.blob())
  } catch {
    error.value = 'Нет связи с сервером'
  } finally {
    loading.value = false
  }
}

watch(() => props.file.id, () => load(props.file), { immediate: true })
onBeforeUnmount(revoke)
</script>

<template>
  <div class="preview">
    <p v-if="loading" class="muted">Открываю…</p>
    <p v-else-if="error" class="msg error">{{ error }}</p>
    <img v-else-if="kind === 'image' && objectUrl" :src="objectUrl" :alt="file.name">
    <iframe v-else-if="kind === 'pdf' && objectUrl" :src="objectUrl" :title="file.name"></iframe>
    <pre v-else-if="kind === 'text'">{{ text }}</pre>
  </div>
</template>
