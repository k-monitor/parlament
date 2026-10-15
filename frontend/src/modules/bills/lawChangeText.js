// Wording shared by the law diff (LawChanges) and its full-law view
// (LawFullDialog), so the list and the dialog describe a change alike.
import { formatDate } from '../../format.js'

// "a 2026. évi LV. törvény 2. § (1) bekezdése" for an adopted bill (njt names
// the act's provision); "a javaslat 2. § (1)" for a computed one.
export function byLine(t, law, act, c) {
  if (!c.provisions?.length) return ''
  const list = c.provisions.join(', ')
  return law.method === 'njt' && act
    ? t('lawChanges.byAct', { act: act.label, provisions: list })
    : t('lawChanges.byBill', { provisions: list })
}

export function stateLine(t, law) {
  if (law.method === 'njt' && law.base && law.target) {
    return t('lawChanges.statesNjt', {
      from: formatDate(law.base.from) || '—', to: formatDate(law.target.from) || '—' })
  }
  if (law.base?.from) return t('lawChanges.stateComputed', { from: formatDate(law.base.from) })
  return ''
}
