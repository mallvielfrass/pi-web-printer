<script setup>
import { inject, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import FileList from '../components/FileList.vue'
import FilePreview from '../components/FilePreview.vue'

const user = inject('user')
const ready = inject('ready')
const files = ref([])
const selected = ref(null)
const q = ref('')
const sort = ref('created_at')
const order = ref('desc')
const error = ref('')

const registered = () => user.value && !user.value.temporary

async function reload() {
  if (!registered()) return
  error.value = ''
  try {
    const data = await api.files({
      q: q.value,
      sort: sort.value,
      order: order.value,
      limit: 200,
    })
    files.value = data.files
    if (selected.value && !files.value.some((item) => item.id === selected.value.id)) {
      selected.value = null
    }
  } catch (err) {
    error.value = err.message
  }
}

function toggleOrder() {
  order.value = order.value === 'desc' ? 'asc' : 'desc'
  reload()
}

watch(sort, reload)
watch(user, reload)

onMounted(reload)
</script>

<template>
  <section v-if="!ready">
    <p class="muted">Загрузка…</p>
  </section>
  <section v-else-if="!registered()" class="panel">
    <h1>Файлы</h1>
    <p>Список всех файлов открывается после регистрации.</p>
    <router-link class="btn primary" to="/login">Войти</router-link>
  </section>
  <section v-else>
    <h1>Файлы</h1>
    <form class="tools" @submit.prevent="reload">
      <input v-model="q" type="search" placeholder="Поиск по имени" aria-label="Поиск по имени">
      <button type="submit">Найти</button>
      <select v-model="sort" aria-label="Сортировка">
        <option value="created_at">По дате</option>
        <option value="name">По имени</option>
        <option value="kind">По типу</option>
      </select>
      <button type="button" @click="toggleOrder">
        {{ order === 'desc' ? 'По убыванию' : 'По возрастанию' }}
      </button>
    </form>
    <p v-if="error" class="msg error">{{ error }}</p>
    <section v-if="selected" class="panel">
      <h2>{{ selected.name }}</h2>
      <FilePreview :file="selected" />
    </section>
    <FileList
      :files="files"
      show-kind
      :selected-id="selected?.id ?? null"
      @open="selected = $event"
      @deleted="reload"
      @error="error = $event"
    />
  </section>
</template>
