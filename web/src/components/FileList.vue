<script setup>
import { api, downloadUrl } from '../api'

defineProps({
  files: { type: Array, required: true },
  showKind: { type: Boolean, default: false },
  selectedId: { type: Number, default: null },
})

const emit = defineEmits(['open', 'deleted', 'error'])

function formatDate(iso) {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function meta(file, showKind) {
  const date = formatDate(file.created_at)
  if (showKind) return date
  return (file.kind === 'scan' ? 'отсканирован ' : 'загружен ') + date
}

function kindLabel(kind) {
  return kind === 'scan' ? 'сканирование' : 'печать'
}

async function remove(file) {
  if (!window.confirm(`Удалить файл «${file.name}»?`)) return
  try {
    await api.deleteFile(file.id)
    emit('deleted', file)
  } catch (err) {
    emit('error', err.message)
  }
}
</script>

<template>
  <ul v-if="files.length" class="files">
    <li
      v-for="file in files"
      :key="file.id"
      class="file"
      :class="{ selected: file.id === selectedId }"
    >
      <button type="button" class="file-main" @click="$emit('open', file)">
        <span class="file-name">{{ file.name }}</span>
        <span class="file-meta">
          <span
            v-if="showKind"
            class="flag"
            :class="file.kind === 'scan' ? 'flag-scan' : 'flag-print'"
          >{{ kindLabel(file.kind) }}</span>
          {{ meta(file, showKind) }}
        </span>
      </button>
      <div class="file-actions">
        <a class="icon-btn" :href="downloadUrl(file.id)" :aria-label="'Скачать ' + file.name" title="Скачать">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10m0 0 4-4m-4 4-4-4M5 19h14" /></svg>
        </a>
        <router-link
          class="icon-btn"
          :to="{ path: '/print', query: { file: String(file.id) } }"
          :aria-label="'Печатать ' + file.name"
          title="Печатать"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 9V4h10v5M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v6H7z" /></svg>
        </router-link>
        <button type="button" class="icon-btn" :aria-label="'Удалить ' + file.name" title="Удалить" @click="remove(file)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M10 7V5h4v2m-6 0 1 12h6l1-12" /></svg>
        </button>
      </div>
    </li>
  </ul>
  <p v-else class="muted">Пока пусто</p>
</template>
