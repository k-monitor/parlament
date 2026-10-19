// A motion (módosító javaslat, egységes javaslat, …) has no page of its own: it
// is an entry in its bill's list of non-self-standing irományok, addressed by an
// anchor named after its number ("779/4" → #iromany-779-4). A vote on a motion
// links there, and the bill page opens the entry the address names.

export function motionAnchor(number) {
  return 'iromany-' + String(number).replace(/[^0-9A-Za-z]+/g, '-')
}

export function motionLocation(billId, number, query) {
  return { name: 'bill', params: { id: billId }, query, hash: '#' + motionAnchor(number) }
}
