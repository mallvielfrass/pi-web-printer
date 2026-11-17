// Все запросы к серверу. Остальной интерфейс сюда не ходит напрямую.

async function request(url, options = {}) {
  let response
  try {
    response = await fetch(url, { credentials: 'same-origin', ...options })
  } catch {
    throw new Error('Нет связи с сервером')
  }
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      throw new Error(response.ok ? 'Сервер вернул не JSON' : 'Ошибка запроса')
    }
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : 'Ошибка запроса'
    throw new Error(detail)
  }
  return data
}

function jsonOptions(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function downloadUrl(id) {
  return `/api/files/${id}/download`
}

export function previewUrl(id) {
  return `/api/files/${id}/preview`
}

export const api = {
  me() {
    return request('/api/auth/me')
  },
  login(login, password) {
    return request('/api/auth/login', jsonOptions('POST', { login, password }))
  },
  register(payload) {
    return request('/api/auth/register', jsonOptions('POST', payload))
  },
  logout() {
    return request('/api/auth/logout', { method: 'POST' })
  },
  files(params = {}) {
    const search = new URLSearchParams()
    for (const key of ['kind', 'q', 'sort', 'order', 'limit']) {
      const value = params[key]
      if (value !== undefined && value !== null && value !== '') {
        search.set(key, String(value))
      }
    }
    const query = search.toString()
    return request('/api/files' + (query ? `?${query}` : ''))
  },
  file(id) {
    return request(`/api/files/${id}`)
  },
  upload(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/api/files', { method: 'POST', body: form })
  },
  deleteFile(id) {
    return request(`/api/files/${id}`, { method: 'DELETE' })
  },
  printer() {
    return request('/api/printer')
  },
  print(body) {
    return request('/api/print', jsonOptions('POST', body))
  },
  job(id) {
    return request(`/api/jobs/${id}`)
  },
  scan(body) {
    return request('/api/scan', jsonOptions('POST', body))
  },
}
