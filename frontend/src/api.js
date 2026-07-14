async function req(path, options) {
  const res = await fetch(path, options)
  if (!res.ok) throw new Error(`${options?.method || 'GET'} ${path} → ${res.status}`)
  return res.json()
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const getMe = () => req('/api/me')
export const getFeed = (period) => req(`/api/feed?period=${period}`)
export const getDigest = (period) => req(`/api/digest?period=${period}`)
export const getShipping = () => req('/api/shipping')
export const getSettings = () => req('/api/settings')
export const putSettings = (settings) => req('/api/settings', json('PUT', settings))
export const postEntries = (entries) => req('/api/entries', json('POST', { entries }))
export const postResolve = (id) => req(`/api/entries/${id}/resolve`, { method: 'POST' })
export const postCleanup = (text) => req('/api/cleanup', json('POST', { text }))
