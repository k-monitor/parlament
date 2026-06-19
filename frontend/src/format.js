// Shared formatting helpers (durations, dates, agenda labels).
import { i18n } from './i18n.js'

export function formatDuration(seconds) {
  if (seconds == null) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

// Compact "Xó Yp" / "Xh Ym" speaking-time label.
export function formatSpeakingTime(seconds) {
  if (!seconds) return '0p'
  const lang = i18n.global.locale.value
  const m = Math.round(seconds / 60)
  if (m < 60) return lang === 'en' ? `${m}m` : `${m}p`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return lang === 'en' ? `${h}h ${rem}m` : `${h}ó ${rem}p`
}

export function formatDate(iso) {
  if (!iso) return ''
  return iso.slice(0, 10)
}

export function agendaLabel(type) {
  if (!type) return ''
  const key = `agendaTypes.${type}`
  const translated = i18n.global.t(key)
  return translated === key ? type : translated
}

// HH:MM:SS for a video offset, as a deep-link-friendly seconds string too.
export function timeLabel(seconds) {
  return formatDuration(seconds)
}
