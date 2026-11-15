<script setup>
import { onMounted, provide, ref } from 'vue'
import { api } from './api'

const user = ref(null)
const ready = ref(false)

async function refreshUser() {
  const data = await api.me()
  user.value = data.user
}

provide('user', user)
provide('ready', ready)
provide('refreshUser', refreshUser)

onMounted(async () => {
  try {
    await refreshUser()
  } finally {
    ready.value = true
  }
})
</script>

<template>
  <div class="wrap">
    <header class="top">
      <router-link class="brand" to="/">Принтер</router-link>
      <nav>
        <router-link v-if="user && !user.temporary" to="/files">Файлы</router-link>
        <router-link to="/login">
          {{ user && !user.temporary ? (user.nickname || user.email) : 'Войти' }}
        </router-link>
      </nav>
    </header>
    <router-view />
  </div>
</template>
