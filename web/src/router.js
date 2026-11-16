import { createRouter, createWebHistory } from 'vue-router'
import FilesView from './views/FilesView.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import PrintView from './views/PrintView.vue'
import ScanView from './views/ScanView.vue'

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior() {
    return { top: 0 }
  },
  routes: [
    { path: '/', component: HomeView, meta: { title: 'Принтер' } },
    { path: '/print', component: PrintView, meta: { title: 'Печать' } },
    { path: '/scan', component: ScanView, meta: { title: 'Сканирование' } },
    { path: '/files', component: FilesView, meta: { title: 'Файлы' } },
    { path: '/login', component: LoginView, meta: { title: 'Вход' } },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title || 'Принтер'
})
