// Copy text to the clipboard, with a fallback for the cases the async Clipboard
// API doesn't cover: non-secure contexts (plain http, e.g. a LAN preview) and
// older browsers, where `navigator.clipboard` is simply absent.
//
// Returns whether the copy succeeded, so the caller can show a confirmation only
// when there is something to confirm — a blocked clipboard must not flash
// "copied!" over text that never made it.
export async function copyText(text) {
  if (!text) return false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    return true
  } catch {
    return false
  }
}
