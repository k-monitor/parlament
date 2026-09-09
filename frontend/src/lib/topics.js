// The CAP major topics as the UI presents them (TOPIC-1..8).
//
// Shared because the same 22 labels are now rendered in three places — the chip
// on a speech or an iromány, and the topic filter on the two iromány lists — and
// a glyph or a name that differed between the chip and the filter that selects
// it would read as two different things.
//
// Keyed by the English label the API sends, which is the model's own label space
// (see backend/app/parlacap.py LABELS), rather than by CAP code: an unmapped
// label then degrades to a neutral glyph instead of borrowing a wrong one.

export const TOPIC_GLYPHS = {
  Macroeconomics: '📈',
  'Civil Rights': '✊',
  Health: '🏥',
  Agriculture: '🌾',
  Labor: '👷',
  Education: '🎓',
  Environment: '🌍',
  Energy: '⚡',
  Immigration: '🛂',
  Transportation: '🚆',
  'Law and Crime': '⚖️',
  'Social Welfare': '🤝',
  Housing: '🏘️',
  'Domestic Commerce': '🏦',
  Defense: '🛡️',
  Technology: '💻',
  'Foreign Trade': '🚢',
  'International Affairs': '🌐',
  'Government Operations': '🏛️',
  'Public Lands': '🏞️',
  Culture: '🎭',
  Other: '💬',
}

export const TOPIC_FALLBACK_GLYPH = '🏷️'

export function topicGlyph(label) {
  return TOPIC_GLYPHS[label] || TOPIC_FALLBACK_GLYPH
}

// Translate a topic by its English label. An unknown one (a model whose label
// space grew) falls back to the label itself rather than showing the reader a
// raw i18n key. `t`/`te` come from the calling component's useI18n().
export function topicName(label, t, te) {
  const key = `topics.names.${label}`
  return te(key) ? t(key) : label
}
