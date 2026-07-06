import { createI18n } from 'vue-i18n'
import hu from './locales/hu.js'
import en from './locales/en.js'

const LANGS = ['hu', 'en']

// localStorage can throw (private mode / "block all cookies") — and this module
// runs at import time, so an unguarded access would white-screen the whole app.
function storedLang() {
  try {
    return localStorage.getItem('ogyw-lang')
  } catch {
    return null
  }
}

// Hungarian is the default (I18N-1); `?lang=en` or a stored choice switches.
// Only known locales are accepted — a bogus ?lang= must not become the active
// locale (vue-i18n would silently fall back while <html lang> lies).
const fromQuery = (new URLSearchParams(location.search).get('lang') || '').toLowerCase()
const locale = [fromQuery, storedLang()].find((l) => LANGS.includes(l)) || 'hu'

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale,
  fallbackLocale: 'hu',
  messages: { hu, en },
})

export function setLocale(lang) {
  if (!LANGS.includes(lang)) return
  i18n.global.locale.value = lang
  try {
    localStorage.setItem('ogyw-lang', lang)
  } catch {
    /* storage unavailable — in-memory locale still switched */
  }
  document.documentElement.lang = lang
}

document.documentElement.lang = locale
