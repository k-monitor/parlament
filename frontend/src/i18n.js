import { createI18n } from 'vue-i18n'
import hu from './locales/hu.js'
import en from './locales/en.js'

// Hungarian is the default (I18N-1); `?lang=en` or a stored choice switches.
const stored = localStorage.getItem('ogyw-lang')
const fromQuery = new URLSearchParams(location.search).get('lang')
const locale = fromQuery || stored || 'hu'

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale,
  fallbackLocale: 'hu',
  messages: { hu, en },
})

export function setLocale(lang) {
  i18n.global.locale.value = lang
  localStorage.setItem('ogyw-lang', lang)
  document.documentElement.lang = lang
}

document.documentElement.lang = locale
