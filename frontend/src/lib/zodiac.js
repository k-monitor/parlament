// Astrological signs (REP-16) — turning the API's language-neutral keys
// ("taurus", "dragon") into something printable, for the profile and the
// comparison alike.
//
// The keys come from the scraper (`scraper/parlamonitor/zodiac.py`), which derives
// them from the Wikidata birth date; nothing here computes a sign, and nothing here
// sees a birth date — the endpoints deliberately don't serve one. Labels live in the
// locale files (I18N-1), so this module owns only the glyphs and the lookup.
import { i18n } from '../i18n.js'

// The twelve sun signs and the twelve animals of the Chinese year, in the order
// `zodiac.py` lists them. The glyphs are decoration: every caller prints the label
// too, and marks the glyph `aria-hidden` (A11Y-1).
const SUN_GLYPHS = {
  aries: '♈', taurus: '♉', gemini: '♊', cancer: '♋',
  leo: '♌', virgo: '♍', libra: '♎', scorpio: '♏',
  sagittarius: '♐', capricorn: '♑', aquarius: '♒', pisces: '♓',
}
const ANIMAL_GLYPHS = {
  rat: '🐀', ox: '🐂', tiger: '🐅', rabbit: '🐇',
  dragon: '🐉', snake: '🐍', horse: '🐎', goat: '🐐',
  monkey: '🐒', rooster: '🐓', dog: '🐕', pig: '🐖',
}

/** One sign as `{ key, glyph, label }`, or null when the key is missing or is one
 *  this build has no label for. An unknown key returns null rather than the raw key:
 *  a sign nobody can read is worse than no sign, and it would print as an i18n path.
 *  `kind` is 'sun' or 'animal'. */
function sign(kind, key) {
  if (!key) return null
  const glyphs = kind === 'sun' ? SUN_GLYPHS : ANIMAL_GLYPHS
  const glyph = glyphs[key]
  if (!glyph) return null
  const path = `profile.${kind === 'sun' ? 'zodiacSign' : 'chineseSign'}.${key}`
  const label = i18n.global.t(path)
  return label === path ? null : { key, glyph, label }
}

/** Both of a person's signs from their API record: `{ sun, animal }`, either side
 *  null when unknown. Null overall when neither is known — for a profile that means
 *  "print no line at all", while the comparison prints "nincs adat" per cell,
 *  because a table cell has neighbours to line up with. */
export function signsOf(person) {
  if (!person) return null
  const sun = sign('sun', person.zodiac_sign)
  const animal = sign('animal', person.chinese_zodiac_sign)
  return sun || animal ? { sun, animal } : null
}

export { sign }
