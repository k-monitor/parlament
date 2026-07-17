<script setup>
// Renders a run of transcript text with recognized person/institution names
// wrapped as inline links (NEL, §10). `entities` is the speech's resolved list
// ([{ surface, kind, ambiguous, links: [{ type, url|person_id, label, description }] }]);
// linkifyEntities matches their exact surfaces in `text` and returns plain/link
// tokens. Each recognized name is shown as the name followed by a small cluster of
// destination BADGES — one per resolved link, in probability order: an internal MP
// profile (👤), K-Monitor tag pages (the K-Monitor logo — the primary source), or
// Wikipedia (its serif "W" — the fallback). Ambiguous matches (more than one
// candidate) get a dotted underline and surface each alternative as its own badge.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { linkifyEntities } from '../format.js'

const props = defineProps({
  text: { type: String, default: '' },
  entities: { type: Array, default: () => [] },
})
const { t } = useI18n()

// TEMPORARY SWITCH: Wikipedia badges are hidden in the transcript while we work
// out some quality issues with the Wikipedia entity links. Flip back to `true`
// to re-enable them — nothing else needs to change.
const SHOW_WIKIPEDIA_BADGES = false

const tokens = computed(() => {
  const toks = linkifyEntities(props.text, props.entities)
  if (SHOW_WIKIPEDIA_BADGES) return toks
  // Strip Wikipedia links. A name whose ONLY destination was Wikipedia degrades
  // to plain text so it isn't left underlined with nowhere to go.
  return toks.map((tok) => {
    if (tok.t !== 'link') return tok
    const links = (tok.entity.links || []).filter((l) => l.type !== 'wikipedia')
    if (!links.length) return { t: 'text', value: tok.value }
    return { ...tok, entity: { ...tok.entity, links } }
  })
})

// The K-Monitor brand mark (public/ asset, cropped to the circular "K").
const KMONITOR_ICON = '/kmonitor-badge.png'

function badgeTitle(link) {
  const src = t(`entity.${link.type}`)
  const base = link.description ? `${link.label} — ${link.description}` : (link.label || '')
  return base ? `${src}: ${base}` : src
}
</script>

<template><template v-for="(tok, i) in tokens" :key="i"><template v-if="tok.t === 'text'">{{ tok.value }}</template><span
    v-else-if="tok.t === 'time'" class="time-chip" :title="t('entity.timeMarker')"
  ><svg class="time-chip__clock" viewBox="0 0 24 24" aria-hidden="true"><circle
    cx="12" cy="12" r="9" /><path d="M12 7.5V12l3 1.8" /></svg>{{ tok.label }}</span><span
    v-else class="entity" :class="{ ambiguous: tok.entity.ambiguous }"
    :title="tok.entity.ambiguous ? t('entity.uncertain') : null"
  ><span class="entity-name">{{ tok.value }}</span><template
      v-for="(link, j) in (tok.entity.links || [])" :key="j"><router-link
        v-if="link.type === 'profile'" class="entity-badge entity-badge--profile"
        :to="{ name: 'profile', params: { id: link.person_id } }"
        :aria-label="badgeTitle(link)" :title="badgeTitle(link)" @click.stop
      ><svg class="entity-badge__person" viewBox="0 0 24 24" aria-hidden="true"><path
        d="M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0 2.2c-4.42 0-8 2.35-8 5.25V21h16v-1.55c0-2.9-3.58-5.25-8-5.25Z"
      /></svg></router-link><a
        v-else-if="link.type === 'kmonitor'" class="entity-badge entity-badge--kmonitor"
        :href="link.url" target="_blank" rel="noopener"
        :aria-label="badgeTitle(link)" :title="badgeTitle(link)" @click.stop
      ><img class="entity-badge__logo" :src="KMONITOR_ICON" alt="" /></a><a
        v-else class="entity-badge entity-badge--wikipedia"
        :href="link.url" target="_blank" rel="noopener"
        :aria-label="badgeTitle(link)" :title="badgeTitle(link)" @click.stop
      ><span class="entity-badge__w" aria-hidden="true">W</span></a></template></span></template></template>

<style scoped>
/* A recognized name in transcript body text: subtly underlined so it reads as an
   entity, with a small cluster of destination badges after it. Ambiguous matches
   (several candidates) get a dotted underline; each candidate is its own badge. */
.entity { white-space: normal; }

/* A wall-clock stamp the record inserts periodically — "(15.30)" — shown as a
   small, quiet clock chip so it reads as a timestamp aside rather than literal
   parenthesised text in the flow of speech. */
.time-chip {
  display: inline-flex; align-items: center; gap: .3em;
  padding: .04em .5em .04em .42em; margin: 0 .1em;
  border-radius: 999px; vertical-align: baseline;
  background: var(--accent-soft); color: var(--muted);
  font-size: .82em; font-weight: 600; font-variant-numeric: tabular-nums;
  line-height: 1.45; white-space: nowrap;
}
.time-chip__clock {
  width: 1em; height: 1em; flex: none; fill: none; stroke: currentColor;
  stroke-width: 2; stroke-linecap: round; stroke-linejoin: round;
}
.entity-name { text-decoration: underline; text-decoration-color: var(--accent);
  text-decoration-thickness: 1px; text-underline-offset: 2px; }
.entity.ambiguous .entity-name { text-decoration-style: dotted; }

/* Destination badges — small square "logo chips", one per link, sitting on the
   text baseline just after the name. Consistent frame (white card + hairline) so
   the K-Monitor logo, Wikipedia "W" and the profile glyph read as one set. */
.entity-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.05em; height: 1.05em; margin-left: 2px; vertical-align: middle;
  border-radius: 4px; overflow: hidden; text-decoration: none;
  border: 1px solid var(--line); background: var(--surface);
  transition: border-color .12s, box-shadow .12s;
}
.entity-badge:hover { border-color: var(--accent);
  box-shadow: 0 1px 3px rgba(0, 0, 0, .18); text-decoration: none; }

.entity-badge__logo { width: 100%; height: 100%; object-fit: contain; display: block; }
/* Wikipedia's mark is a serif "W" (its wordmark/favicon) — set it in a serif face. */
.entity-badge__w { font-family: Georgia, "Times New Roman", serif; font-weight: 700;
  font-size: .8em; line-height: 1; color: var(--ink); }
/* Internal MP profile — our own page: a clean person glyph in the brand accent. */
.entity-badge--profile { color: var(--accent); }
.entity-badge__person { width: 72%; height: 72%; display: block; fill: currentColor; }
.entity-badge--profile:hover .entity-badge__person { fill: var(--accent); }
</style>
