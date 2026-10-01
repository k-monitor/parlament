<script setup>
// The contents of one EVNYR asset declaration (REP-18): what the House's own
// declaration page shows, section by section, in its order. The API serves the
// declaration as upstream's paths ("kovetelesek.ertekpapirok") mapped to lists
// of entries or to a single text, and the sections below follow the system's
// own form (Part A assets, B income, C interests).
//
// Values are shown exactly as filed. Amounts are free text upstream ("Aktuális
// érték: 211448,29 EUR (77083474 HUF)"), so nothing here is summed, converted
// or reformatted: a total we computed would be a figure the declarant never
// gave (TRUST-1). Only dates are localised, and only when they are ISO dates.
//
// A path or field this map does not know is still shown, under its own key: the
// form gains fields over time (`schemaVersion`), and dropping what we cannot
// label would hide part of a declaration without saying so.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDateLocal } from '../../format.js'

const props = defineProps({ content: { type: Object, default: () => ({}) } })
const { t, te } = useI18n()

const SECTIONS = [
  { id: 'realEstate', blocks: [
    { path: 'ingatlanok', fields: ['telepules', 'teruletNagysag', 'muvelesiAg', 'epuletJelleg',
      'alapterulet', 'jogiJelleg', 'jogallas', 'tulajdoniHanyad', 'szerzesJogcime',
      'jogviszonyKezdete', 'szerzesEllenerteke'] },
  ] },
  { id: 'movables', blocks: [
    { path: 'nagyErtekuIngosagok.szemelygepjarmuvek', fields: ['jarmuTipusa', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.tehergepjarmuvek', fields: ['jarmuTipusa', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.motorkerekparok', fields: ['jarmuTipusa', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.viziLegiJarmuvek', fields: ['jarmuJellege', 'jarmuTipusa', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.vedettMualkotasok', fields: ['megnevezes', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.vedettGyujtemenyek', fields: ['megnevezes', 'szerzesIdeje', 'szerzesJogcime'] },
    { path: 'nagyErtekuIngosagok.egyebIngosagok', fields: ['megnevezes', 'szerzesIdeje', 'szerzesJogcime'] },
  ] },
  { id: 'claims', blocks: [
    { path: 'kovetelesek.ertekpapirok', fields: ['megnevezes', 'isin', 'nevErtek', 'atvaltasiArfolyam'] },
    { path: 'kovetelesek.szamlakovetelesek', fields: ['megnevezes', 'swiftBic', 'osszeg', 'atvaltasiArfolyam'] },
    { path: 'kovetelesek.keszpenz', fields: ['osszeg', 'atvaltasiArfolyam'] },
    { path: 'kovetelesek.szerzodesesKovetelesek', fields: ['megnevezes', 'osszeg', 'atvaltasiArfolyam', 'szerzodesIdeje'] },
  ] },
  { id: 'debts', blocks: [
    { path: 'tartozasok.koztartozasok', fields: ['jelleg', 'osszeg', 'atvaltasiArfolyam'] },
    { path: 'tartozasok.hitelintezetiTartozasok', fields: ['osszeg', 'atvaltasiArfolyam'] },
    { path: 'tartozasok.maganszemelyiTartozasok', fields: ['osszeg', 'atvaltasiArfolyam'] },
  ] },
  { id: 'gifts', blocks: [
    { path: 'protokollAjandekok', fields: ['megnevezes', 'ajandekozoNeve'] },
  ] },
  { id: 'otherNotes', blocks: [{ path: 'egyebKozlendok' }] },
  { id: 'income', blocks: [
    { path: 'jovedelemnyilatkozat.korabbiFoglalkozasok', fields: ['megnevezes', 'reszesultDijazasban', 'haviJovedelem', 'atvaltasiArfolyam'] },
    { path: 'jovedelemnyilatkozat.jelenlegiTevekenysegek', fields: ['megnevezes', 'kifizetoSzemelye', 'reszesulDijazasban', 'haviJovedelem', 'atvaltasiArfolyam'] },
  ] },
  { id: 'interests', blocks: [
    { path: 'gazdasagiErdekeltseg.tagsagok', fields: ['szervezetNeve', 'tagsag', 'reszesulDijazasban', 'haviJovedelem', 'atvaltasiArfolyam'] },
    { path: 'gazdasagiErdekeltseg.tarsasagiErdekeltsegek', fields: ['tarsasagNeve', 'erdekeltsegFormaja', 'tulajdoniArany', 'reszesulDijazasban', 'haviJovedelem', 'atvaltasiArfolyam'] },
    { path: 'gazdasagiErdekeltseg.egyebErdekek', fields: ['leiras'] },
    { path: 'gazdasagiErdekeltseg.nyilatkozattetelHelye' },
  ] },
]
const DATE_FIELDS = new Set(['jogviszonyKezdete', 'szerzesIdeje', 'szerzodesIdeje'])
const YES_NO_FIELDS = new Set(['reszesultDijazasban', 'reszesulDijazasban'])
const KNOWN_PATHS = new Set(SECTIONS.flatMap((s) => s.blocks.map((b) => b.path)))

// Message keys cannot contain dots, upstream paths do.
const keyOf = (path) => path.replace(/\./g, '_')
const label = (kind, key) => (te(`declaration.${kind}.${key}`) ? t(`declaration.${kind}.${key}`) : key)

function rowsOf(item, fields = []) {
  const keys = [...fields.filter((f) => item[f] != null), ...Object.keys(item).filter((f) => !fields.includes(f))]
  return keys.map((f) => ({ key: f, label: label('fields', f), value: valueOf(f, item[f]) }))
}
function valueOf(field, value) {
  if (DATE_FIELDS.has(field) && /^\d{4}-\d{2}-\d{2}/.test(value)) return formatDateLocal(value)
  if (YES_NO_FIELDS.has(field)) {
    const v = String(value).trim().toLowerCase()
    if (v === 'igen') return t('declaration.yes')
    if (v === 'nem') return t('declaration.no')
  }
  return value
}
function blockOf(path, fields) {
  const v = props.content?.[path]
  if (v == null || v === '' || (Array.isArray(v) && !v.length)) return null
  return {
    path,
    title: label('blocks', keyOf(path)),
    text: Array.isArray(v) ? null : String(v),
    items: Array.isArray(v) ? v.map((item) => rowsOf(item, fields)) : [],
  }
}

const sections = computed(() => {
  // `single`: the section IS its one block (Ingatlanok), so the block needs no
  // heading of its own. Decided by the form, not by what was filled in: a lone
  // car under "Nagy értékű ingóságok" still has to say it is a car.
  const out = SECTIONS.map((s) => ({
    id: s.id, title: t(`declaration.sections.${s.id}`), single: s.blocks.length === 1,
    blocks: s.blocks.map((b) => blockOf(b.path, b.fields)).filter(Boolean),
  }))
  const unknown = Object.keys(props.content || {}).filter((p) => !KNOWN_PATHS.has(p))
  out.push({ id: 'unknown', title: t('declaration.sections.unknown'), single: false,
    blocks: unknown.map((p) => blockOf(p, [])).filter(Boolean) })
  return out.filter((s) => s.blocks.length)
})
</script>

<template>
  <div class="decl">
    <p v-if="!sections.length" class="muted small">{{ $t('declaration.empty') }}</p>
    <section v-for="s in sections" :key="s.id" class="decl-sec">
      <!-- A one-block section is its block: the count goes on the section. -->
      <h4>
        {{ s.title }}
        <span v-if="s.single && s.blocks[0].items.length" class="muted">({{ s.blocks[0].items.length }})</span>
      </h4>
      <div v-for="b in s.blocks" :key="b.path" class="decl-block">
        <h5 v-if="!s.single">
          {{ b.title }} <span v-if="b.items.length" class="muted">({{ b.items.length }})</span>
        </h5>
        <p v-if="b.text" class="decl-text">{{ b.text }}</p>
        <dl v-for="(rows, i) in b.items" :key="i" class="decl-item">
          <template v-for="r in rows" :key="r.key">
            <dt>{{ r.label }}</dt>
            <dd>{{ r.value }}</dd>
          </template>
        </dl>
      </div>
    </section>
  </div>
</template>

<style scoped>
.decl { margin-top: .5rem; }
.decl-sec + .decl-sec { margin-top: .9rem; }
.decl h4 { margin: 0 0 .35rem; font-size: .9rem; }
.decl h5 { margin: .5rem 0 .3rem; font-size: .8rem; font-weight: 600; color: var(--ink-soft); }
/* One entry per small block: label above value on a phone, side by side when
   there is room. ISIN lists and long descriptions wrap rather than widen the
   card. */
.decl-item {
  margin: 0 0 .45rem; padding: .4rem .55rem; border: 1px solid var(--line);
  border-radius: 6px; display: grid; grid-template-columns: minmax(7rem, 38%) 1fr;
  gap: .15rem .7rem; font-size: .8rem;
}
.decl-item dt { color: var(--ink-faint); }
.decl-item dd { margin: 0; overflow-wrap: anywhere; white-space: pre-line; }
.decl-text { margin: 0 0 .45rem; font-size: .8rem; white-space: pre-line; overflow-wrap: anywhere; }
@media (max-width: 520px) {
  .decl-item { grid-template-columns: 1fr; }
  .decl-item dd { margin-bottom: .25rem; }
}
</style>
