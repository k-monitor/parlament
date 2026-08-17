<script setup>
// Where the House looks — mention counts for every settlement in the country, and
// the ones it never names (§6D TEL-6/TEL-7), in any of three binnings (TEL-15/TEL-16).
//
// **Points** (the default) answer *which places*: the unit of the data is a
// settlement, and a reader looking for their own town has to be able to find it. But
// a point map is a poor picture of *where* attention falls — it encodes a quantity as
// area and then lets the marks overlap, so the densely-settled middle of the country
// reads as loud whatever the numbers say, and 3 178 marks leave the eye no regional
// pattern.
//
// **Segments** answer that instead — the settlements aggregated into an area and the
// area shaded — and the two available bins are deliberately different kinds of area:
//
// - **H3 hexagons** are equal-*area*, which is what stops a cell reading as loud
//   merely for being large. The geographically honest picture of *where*.
// - **Constituencies** are equal-*electorate* instead, and each has a member who can
//   be asked about it. The politically honest picture of *whose* — at the cost that a
//   rural constituency covers many more pixels than an urban one, which is the flaw
//   the hexagons do not have. The two views cover each other's weakness, which is why
//   both are offered rather than one being chosen.
//
// Either arrives from the server as ordinary GeoJSON, so this component needs no
// geometry library of its own.
//
// **The scale is compressed on purpose** in the mention reading. Mentions span four
// orders of magnitude — Budapest against a village named once — so a linear scale
// draws one big dot and 3 176 identical specks. Radius and shade both follow
// log(1+n), and the legend states the bands, so the reader is never asked to compare
// areas by eye. A *share* (the blind reading of a segment) is not compressed: it is
// already bounded, and log-scaling a percentage would misstate it.
//
// Magnitude is carried **twice** wherever it can be — by size and by shade of one hue
// (the site's own accent, the same sequential ramp the activity heatmap uses) — so
// nothing is lost in greyscale or to any colour-vision deficiency (A11Y-1), and the
// ranked list beside the map is the table view of the same data. Silence is shaded on
// a **neutral** ramp instead, never a pale red: it is not a small quantity of speech,
// and reusing the speech hue for it would say that it is.
//
// Leaflet and its stylesheet load **on demand**, as they do for the constituency map:
// this is one page of the site and the mapping library must not weigh on any other.
// Marks are drawn on a **canvas** renderer — 3 177 individual SVG nodes is where a map
// like this normally dies.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  // Positional rows from /settlements/map: [id, name, county, lat, lon, mentions].
  points: { type: Array, required: true },
  // { tile_url, tile_attribution, max_zoom } — basemap, configured server-side so
  // the attribution the tile provider requires always travels with the URL.
  map: { type: Object, required: true },
  max: { type: Number, default: 0 },
  // 'mentions' shades by how often a place is named; 'blind' makes the silence the
  // figure rather than the ground.
  mode: { type: String, default: 'mentions' },
  // 'points' | 'h3' | 'oevk' — which binning is on screen (TEL-15/TEL-16).
  bins: { type: String, default: 'points' },
  // The GeoJSON FeatureCollection from /settlements/map/segments, or null while it
  // has not been asked for. Ignored while `bins` is 'points'.
  segments: { type: Object, default: null },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const { t, n: fmtNumber } = useI18n()

const host = ref(null)
const failed = ref(false)
// Leaflet objects are deliberately NOT reactive: large, self-mutating and cyclic.
const leaflet = shallowRef(null)
const mapInstance = shallowRef(null)
const layerGroup = shallowRef(null)

// The sequential ramp for **speech**: one hue, light → dark, four steps — the site's
// accent red (#B22817) at the same intensities the activity heatmap uses, but
// composited onto white rather than left translucent, because over a basemap a
// translucent fill takes its colour from whatever happens to lie beneath it.
const MENTION_STEPS = ['#e8beb9', '#d8938b', '#c86458', '#b22817']
// The sequential ramp for **silence**: the brand's warm dark (#3E3B32) over the same
// four steps. A second single hue, not a second saturation of the first — so a dark
// cell can never be misread as a loud one.
const SILENCE_STEPS = ['#cfcecc', '#a8a7a3', '#787670', '#3e3b32']
// Never named, as a point. A hollow grey ring: absence is a different *kind* of thing
// from a small number, and the ramp must not appear to continue into it.
const BLIND_INK = '#8a857b'
const R_MIN = 2.4
const R_MAX = 13

const segmented = computed(() =>
  !!(props.bins !== 'points' && props.segments && props.segments.features))
// The constituency binning shows more per cell than a hexagon can: it has a name, a
// seat, a member, and settlements it shares with its neighbours.
const territorial = computed(() => props.bins === 'oevk')
const cellMax = computed(() =>
  (props.segments && props.segments.max_mentions) || 0)
// The bottom of the observed range, which for constituencies is nowhere near zero:
// each holds twenty-odd settlements, so the quietest still counts in the dozens, and a
// ramp anchored at zero would spend its palest steps where no cell lives. Hexagons and
// points report (or imply) a floor of zero, so they are unaffected.
const cellFloor = computed(() =>
  (props.segments && props.segments.min_mentions) || 0)

// ---------------------------------------------------------------------------
// Scales
// ---------------------------------------------------------------------------

function intensity(n, max, floor = 0) {
  // log1p so a village named twice is still visibly different from one named once,
  // while the capital does not flatten everything below it. `floor` stretches the ramp
  // over the range the data occupies instead of over [0, max]; at 0 it is a no-op.
  const base = Math.log1p(Math.max(floor, 0))
  const span = Math.log1p(Math.max(max, 1)) - base
  if (span <= 0) return n > 0 ? 1 : 0
  return Math.min(1, Math.max(0, (Math.log1p(n) - base) / span))
}
function stepFor(steps, fraction) {
  return steps[Math.min(steps.length - 1,
                        Math.max(0, Math.floor(fraction * steps.length)))]
}
function radius(n) {
  if (!n) return R_MIN
  return R_MIN + (R_MAX - R_MIN) * intensity(n, props.max)
}

// Bucket boundaries for the legend, from the same log compression as the marks, so
// the legend describes what is drawn rather than a tidy approximation of it.
function mentionBands(max, floor = 0) {
  const top = Math.max(max, 1)
  const base = Math.log1p(Math.max(floor, 0))
  const span = Math.log1p(top) - base
  const edges = [0.25, 0.5, 0.75, 1].map((f) =>
    Math.max(1, Math.round(Math.expm1(base + f * span))))
  let low = Math.max(floor, 1)
  return edges.map((high, i) => {
    const band = { color: MENTION_STEPS[i], low, high }
    low = high + 1
    return band
  }).filter((b, i, all) => i === 0 || b.high > all[i - 1].high)
}

// A share needs no compression and no computed edges — the quarters are the bands.
const SHARE_BANDS = [0, 0.25, 0.5, 0.75].map((low, i) => ({
  color: SILENCE_STEPS[i], low, high: low + 0.25,
}))

const bands = computed(() => {
  if (segmented.value && props.mode === 'blind') return SHARE_BANDS
  return segmented.value
    ? mentionBands(cellMax.value, cellFloor.value)
    : mentionBands(props.max)
})

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

const shownPoints = computed(() =>
  props.mode === 'blind' ? props.points.filter((p) => !p[5]) : props.points)

function drawPoints(L, group) {
  // Drawn quietest-first so the loudest places end up on top and are never buried
  // under the specks around them.
  const rows = [...shownPoints.value].sort((a, b) => a[5] - b[5])
  const blindIsFigure = props.mode === 'blind'
  for (const [id, name, county, lat, lon, mentions] of rows) {
    const blind = !mentions
    // A never-named place is the *figure* in blind mode and the *ground* in the
    // default one. Drawn at equal weight in both, the 2 900 zeroes covered the
    // country and buried the few hundred places the map is about — so here they are
    // a faint speck for context, and there a hollow ring that reads as a mark.
    const marker = L.circleMarker([lat, lon], {
      renderer: group.options.renderer,
      radius: blind ? (blindIsFigure ? R_MIN + 1.4 : 1.5) : radius(mentions),
      // A 1px surface-coloured ring separates overlapping marks, so a cluster of
      // neighbouring villages reads as several places and not one blob.
      color: blind ? BLIND_INK : '#ffffff',
      weight: blind ? (blindIsFigure ? 1.4 : 0) : 1,
      opacity: blind ? (blindIsFigure ? 0.9 : 0.5) : 0.85,
      fillColor: blind ? (blindIsFigure ? '#ffffff' : BLIND_INK)
        : stepFor(MENTION_STEPS, intensity(mentions, props.max)),
      fillOpacity: blind ? (blindIsFigure ? 0.35 : 0.4) : 0.9,
    })
    marker.bindTooltip(
      `<strong>${escapeHtml(name)}</strong>${county ? ` · ${escapeHtml(county)}` : ''}`
      + `<br>${fmtNumber(mentions)} ${escapeHtml(t('settlements.mentionsShort'))}`,
      { direction: 'top', className: 'pm-maptip' })
    marker.on('click', () => emit('select', id))
    marker.addTo(group)
  }
}

function segmentStyle(properties) {
  const blind = props.mode === 'blind'
  const fill = blind
    ? stepFor(SILENCE_STEPS, properties.blind_share || 0)
    : stepFor(MENTION_STEPS,
              intensity(properties.mentions, cellMax.value, cellFloor.value))
  // A cell nobody has named at all in the mention reading is left unfilled rather
  // than given the ramp's lightest step: the ramp measures speech, and there is none.
  const empty = !blind && !properties.mentions
  return {
    // A hairline seam in the surface colour, so neighbouring cells read as separate
    // bins without a grid of dark lines competing with the fills. Drawn a little
    // heavier for constituencies: a hexagon's edge is an artefact of the binning, but
    // a constituency's edge is part of what the map is about.
    color: '#ffffff',
    weight: territorial.value ? 1 : 0.6,
    opacity: 0.9,
    fillColor: empty ? '#ffffff' : fill,
    fillOpacity: empty ? 0.15 : 0.75,
  }
}

function segmentTooltip(properties) {
  const lines = []
  // A constituency is a named thing, so it says its own name; a hexagon has only a
  // number for a name, and saying it would tell the reader nothing.
  if (territorial.value) {
    lines.push(`<strong>${escapeHtml(properties.label)}</strong>`
      + (properties.seat ? ` <span class="tipnames">· ${escapeHtml(properties.seat)}</span>` : ''))
  }
  if (props.mode === 'blind') {
    lines.push(`<strong>${Math.round((properties.blind_share || 0) * 100)}%</strong> `
      + escapeHtml(t('settlements.segmentBlindShare')))
  } else {
    lines.push(`<strong>${fmtNumber(properties.mentions)}</strong> `
      + escapeHtml(t('settlements.mentionsShort')))
  }
  // The denominator, always: a share over one settlement is not a share, and both
  // binnings have cells that hold exactly one (TEL-15/TEL-16).
  lines.push(escapeHtml(t('settlements.segmentCounts', {
    named: properties.named, total: properties.settlements,
  })))
  const names = (properties.top || []).map((s) => escapeHtml(s.name)).join(', ')
  // A cell with no names in it is a shape, not a finding.
  if (names) lines.push(`<span class="tipnames">${names}</span>`)
  if (territorial.value) {
    // The overlap, stated on the cell that has it: a mention names a place, not the
    // part of it that falls in this constituency, so a city split between several is
    // counted in each and the cells do not sum to the national total (TEL-16).
    if (properties.shared) {
      lines.push(`<span class="tipnames">${escapeHtml(
        t('settlements.segmentShared', { n: properties.shared }))}</span>`)
    }
    // Who holds the seat, and how much of it they have named — TEL-9's measure as a
    // fact about this one cell, with its own denominator, never as a shade to compare
    // across cells whose denominators run from 1 settlement to 174.
    if (properties.mp) {
      let mp = escapeHtml(properties.mp.name)
      if (properties.mp.faction) {
        mp += ` <span class="tipnames">(${escapeHtml(properties.mp.faction.label)})</span>`
      }
      if (properties.own_total) {
        mp += `<br><span class="tipnames">${escapeHtml(t('settlements.segmentOwn', {
          named: properties.own_named, total: properties.own_total,
        }))}</span>`
      }
      lines.push(mp)
    }
  }
  return lines.join('<br>')
}

function drawSegments(L, group) {
  L.geoJSON(props.segments, {
    renderer: group.options.renderer,
    style: (feature) => segmentStyle(feature.properties),
    onEachFeature: (feature, sub) => {
      sub.bindTooltip(segmentTooltip(feature.properties),
                      { direction: 'top', className: 'pm-maptip', sticky: true })
      // A cell holds many settlements, so opening one of them would be a guess.
      // Clicking drills into the cell instead — where the point map then names the
      // places inside it.
      sub.on('click', () => {
        const map = mapInstance.value
        if (map && sub.getBounds) map.fitBounds(sub.getBounds(), { padding: [24, 24] })
      })
    },
  }).addTo(group)
}

function draw() {
  const L = leaflet.value
  const group = layerGroup.value
  if (!L || !group) return
  group.clearLayers()
  if (segmented.value) drawSegments(L, group)
  else drawPoints(L, group)
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

async function build() {
  let L
  try {
    await import('leaflet/dist/leaflet.css')
    L = (await import('leaflet')).default
  } catch {
    failed.value = true
    return
  }
  if (!host.value) return
  leaflet.value = L
  const map = L.map(host.value, { scrollWheelZoom: true, zoomControl: true })
  mapInstance.value = map
  L.tileLayer(props.map.tile_url, {
    maxZoom: props.map.max_zoom || 18,
    attribution: props.map.tile_attribution || '',
  }).addTo(map)
  // One canvas for every mark: with 3 177 points (or 1 800 hexagons) an SVG node
  // each is what makes a map like this unusable on a phone.
  layerGroup.value = L.layerGroup([], { renderer: L.canvas({ padding: 0.4 }) }).addTo(map)
  // Hungary, whole. Fitted to the data's own extent rather than to fixed bounds, so
  // a deployment windowed to part of the corpus still frames what it has.
  const lats = props.points.map((p) => p[3])
  const lons = props.points.map((p) => p[4])
  if (lats.length) {
    map.fitBounds([[Math.min(...lats), Math.min(...lons)],
                   [Math.max(...lats), Math.max(...lons)]], { padding: [8, 8] })
  } else {
    map.setView([47.16, 19.5], 7)
  }
  draw()
}

// The view keeps its place when the binning or the reading changes: switching to
// segments re-styles what is on screen, it does not re-frame the country, so a reader
// who has zoomed into their own region stays there.
watch(() => [props.mode, props.bins, props.points, props.segments, props.max],
      draw, { deep: false })
onMounted(build)
onBeforeUnmount(() => {
  if (mapInstance.value) mapInstance.value.remove()
  mapInstance.value = null
})
</script>

<template>
  <figure class="mapfig">
    <p v-if="failed" class="state" role="alert">{{ $t('settlements.mapFailed') }}</p>
    <div
      v-else ref="host" class="mentionmap" role="application"
      :aria-label="ariaLabel || $t('settlements.mapLabel')"
    ></div>
    <!-- The legend is not optional: the mention scale is compressed, so the bands are
         the only way to read a mark back into a number. -->
    <figcaption class="legend small">
      <template v-if="segmented && mode === 'blind'">
        <span class="lkey" v-for="b in bands" :key="b.color">
          <span class="swatch" :class="territorial ? 'region' : 'hex'"
                :style="{ background: b.color }" aria-hidden="true"></span>
          <span>{{ Math.round(b.low * 100) }}–{{ Math.round(b.high * 100) }}%</span>
        </span>
        <span class="muted lscale">{{ territorial
          ? $t('settlements.legendOevkBlind') : $t('settlements.legendSegmentBlind') }}</span>
      </template>
      <template v-else-if="segmented">
        <span class="lkey" v-for="b in bands" :key="b.color">
          <span class="swatch" :class="territorial ? 'region' : 'hex'"
                :style="{ background: b.color }" aria-hidden="true"></span>
          <span>{{ b.low === b.high ? b.low : b.low + '–' + b.high }}</span>
        </span>
        <span class="lkey">
          <span class="swatch empty" :class="territorial ? 'region' : 'hex'"
                aria-hidden="true"></span>
          <span>{{ $t('settlements.legendBlind') }}</span>
        </span>
        <span class="muted lscale">{{ territorial
          ? $t('settlements.legendOevkScale') : $t('settlements.legendSegmentScale') }}</span>
      </template>
      <template v-else-if="mode === 'mentions'">
        <span class="lkey" v-for="b in bands" :key="b.color">
          <span class="swatch" :style="{ background: b.color }" aria-hidden="true"></span>
          <span>{{ b.low === b.high ? b.low : b.low + '–' + b.high }}</span>
        </span>
        <span class="lkey">
          <span class="swatch blind" aria-hidden="true"></span>
          <span>{{ $t('settlements.legendBlind') }}</span>
        </span>
        <span class="muted lscale">{{ $t('settlements.legendScale') }}</span>
      </template>
      <span v-else class="lkey">
        <span class="swatch blind" aria-hidden="true"></span>
        <span>{{ $t('settlements.legendBlindOnly') }}</span>
      </span>
    </figcaption>
  </figure>
</template>

<style scoped>
.mapfig { margin: .6rem 0 0; }
.mentionmap {
  width: 100%; height: 520px;
  border-radius: var(--radius); border: 1px solid var(--line);
  /* Leaflet's panes (400) and controls (1000) carry z-indices of their own; without
     a stacking context here they compete with the sticky site header. */
  isolation: isolate;
  background: var(--bg);
}
@media (max-width: 560px) { .mentionmap { height: 360px; } }
.legend {
  display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .9rem;
  margin-top: .5rem; color: var(--ink-soft);
}
.lkey { display: inline-flex; align-items: center; gap: .3rem; white-space: nowrap; }
.swatch {
  width: .8rem; height: .8rem; border-radius: 999px; border: 1px solid #ffffff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, .12);
}
/* The segment key takes the shape of its binning — a hexagon for the grid, a blunt
   patch for a constituency — so the legend says which one it describes without needing
   a word for it. */
.swatch.hex {
  border-radius: 2px; border: 0; box-shadow: none;
  clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
}
.swatch.region {
  width: 1rem; border-radius: 2px; border: 0; box-shadow: none;
  clip-path: polygon(8% 12%, 62% 0%, 100% 34%, 88% 92%, 34% 100%, 0% 62%);
}
.swatch.empty { background: var(--surface); box-shadow: inset 0 0 0 1px #b9bdc4; }
.swatch.blind { background: #ffffff; border-color: #8a857b; }
.lscale { flex: 1 1 100%; }
</style>

<style>
/* Not scoped: Leaflet appends tooltips to the map pane, outside this component. */
.leaflet-tooltip.pm-maptip {
  background: var(--surface); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; box-shadow: var(--shadow); font-size: .8rem; line-height: 1.3;
  padding: .25rem .45rem;
}
.leaflet-tooltip.pm-maptip strong { font-weight: 700; }
.leaflet-tooltip.pm-maptip .tipnames { color: var(--ink-faint); }
</style>
