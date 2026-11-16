<script setup>
import { inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const user = inject('user')
const refreshUser = inject('refreshUser')
const router = useRouter()
const mode = ref('login')
const login = ref('')
const nickname = ref('')
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    if (mode.value === 'login') {
      await api.login(login.value, password.value)
    } else {
      await api.register({
        nickname: nickname.value,
        email: email.value,
        password: password.value,
      })
    }
    await refreshUser()
    router.push('/')
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function logout() {
  busy.value = true
  error.value = ''
  try {
    await api.logout()
    await refreshUser()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="user && !user.temporary" class="panel">
    <h1>Вы вошли</h1>
    <p>{{ user.nickname || user.email }}</p>
    <button type="button" :disabled="busy" @click="logout">Выйти</button>
  </section>
  <section v-else>
    <div class="tabs">
      <button type="button" :class="{ on: mode === 'login' }" @click="mode = 'login'">Вход</button>
      <button type="button" :class="{ on: mode === 'register' }" @click="mode = 'register'">Регистрация</button>
    </div>
    <p v-if="user?.temporary" class="muted">Файлы гостя перейдут на аккаунт.</p>
    <form class="stack" @submit.prevent="submit">
      <template v-if="mode === 'login'">
        <label>
          Ник или почта
          <input v-model="login" autocomplete="username" required>
        </label>
      </template>
      <template v-else>
        <label>
          Ник
          <input v-model="nickname" autocomplete="username">
        </label>
        <label>
          Почта
          <input v-model="email" type="email" autocomplete="email" placeholder="необязательно">
        </label>
      </template>
      <label>
        Пароль
        <input
          v-model="password"
          type="password"
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          required
        >
      </label>
      <p v-if="error" class="msg error">{{ error }}</p>
      <button type="submit" class="primary" :disabled="busy">
        {{ mode === 'login' ? 'Войти' : 'Создать аккаунт' }}
      </button>
    </form>
  </section>
</template>
