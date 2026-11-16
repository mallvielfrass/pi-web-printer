<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const printer = ref(null)
const error = ref('')
const loading = ref(false)

const percent = computed(() => {
  const match = /^(\d+)%$/.exec(printer.value?.toner || '')
  return match ? Number(match[1]) : null
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    printer.value = await api.printer()
    if (printer.value.message) error.value = printer.value.message
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="panel">
    <div class="status-head">
      <div>
        <h1>{{ printer?.name || 'Принтер' }}</h1>
        <p class="muted">{{ printer?.state || (loading ? 'Загрузка…' : 'неизвестно') }}</p>
      </div>
      <button type="button" :disabled="loading" @click="load">Обновить</button>
    </div>
    <div class="meters">
      <div class="meter">
        <div class="meter-label">Тонер</div>
        <div class="meter-value">{{ printer?.toner || 'неизвестно' }}</div>
        <div v-if="percent !== null" class="bar" aria-hidden="true">
          <span :style="{ width: percent + '%' }"></span>
        </div>
      </div>
      <div class="meter">
        <div class="meter-label">Бумага</div>
        <div class="meter-value">{{ printer?.paper || 'неизвестно' }}</div>
      </div>
    </div>
    <p v-if="error" class="msg error">{{ error }}</p>
  </section>

  <section class="home-actions">
    <router-link class="card card-print" to="/print">Печать</router-link>
    <router-link class="card card-scan" to="/scan">Сканирование</router-link>
  </section>
</template>
