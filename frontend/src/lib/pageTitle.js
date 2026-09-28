// The document <title> across in-app navigation.
//
// The server writes each page's own <title> into the shell it serves (backend
// app/og.py) — but only on a full page load. After that the SPA swaps pages in
// the browser, so without this the tab, the history list and bookmarks all kept
// the title of whichever page the reader happened to land on.
//
// One writer, two sources:
// - a browse page is titled by its route name (`pageTitle.routes.<name>` in the
//   locales, whose Hungarian copies og.py's `_ROUTE_CARDS`);
// - a detail page names itself once its data is in (`usePageTitle`), built the
//   way og.py builds that page's card, so a crawler that renders the page reads
//   the same title the server already sent.
// Every title is suffixed with the site name, as og.py's `render` does.
import { onBeforeUnmount, ref, shallowRef, watchEffect } from 'vue'
import { START_LOCATION } from 'vue-router'
import { i18n } from '../i18n.js'
import { formatLongDate } from '../format.js'

const SITE = 'Parlamonitor'

// The mounted view's own title getter, if it has one.
const viewTitle = shallowRef(null)
// Whether the reader has moved off the page the server rendered. Until then a
// detail page's title is already the right one, so it stays up while the page's
// data loads instead of dropping to the bare site name and back.
const leftLanding = ref(false)

// Called from a detail view's setup: `getter` returns the page's title, or ''
// while its data is not in yet. Reactive — re-read whenever what it reads changes.
export function usePageTitle(getter) {
  viewTitle.value = getter
  onBeforeUnmount(() => { if (viewTitle.value === getter) viewTitle.value = null })
}

// A date as the title spells it: og.py's `hu_date` ("2026. június 18.") in
// Hungarian, the locale's long date otherwise.
export function titleDate(iso) {
  return iso ? formatLongDate(String(iso).slice(0, 10), i18n.global.locale.value) : ''
}

// og.py's `_truncate`, for the titles it shortens.
export function clipTitle(text, limit = 120) {
  const s = String(text || '').split(/\s+/).filter(Boolean).join(' ')
  return s.length <= limit ? s : s.slice(0, limit).trimEnd() + '…'
}

export function installPageTitle(router) {
  router.afterEach((to, from, failure) => {
    // A query change (a filter, a page of results, `?s=`) is the same page.
    if (!failure && from !== START_LOCATION && from.path !== to.path) leftLanding.value = true
  })
  watchEffect(() => {
    const route = router.currentRoute.value
    if (route === START_LOCATION) return
    const { t, te } = i18n.global
    const key = `pageTitle.routes.${String(route.name)}`
    const own = viewTitle.value ? viewTitle.value() : ''
    const title = own || (te(key) ? t(key) : '')
    if (!title && !leftLanding.value) return
    document.title = !title ? SITE : title.endsWith(SITE) ? title : `${title} · ${SITE}`
  })
}
