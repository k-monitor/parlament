<script setup>
// Map picker for a settlement that spans several single-member constituencies
// (REP-10). Drawn over a street basemap, because the reader answers the question
// by recognising their own neighbourhood — an abstract polygon tells them nothing.
//
// The constituency polygons partition the settlement, so the reader's own part is
// unambiguous once they can see where the boundary runs. The polygons extend well
// past the settlement (a constituency covers several districts), so the view is
// fitted to the *settlement's* outline and the rest is simply off screen; the
// outline itself is drawn on top as a dashed line, to make clear which shape is
// "your town" and which are the constituencies dividing it.
//
// Leaflet and its stylesheet are loaded **on demand**: this is one page of the
// site, so the mapping library must not weigh on any other (cf. the on-demand
// codec in ExportDialog).
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import {
  CONSTITUENCY_FILL, CONSTITUENCY_INK, FILL_OPACITY, FILL_OPACITY_SELECTED,
} from '../lib/constituencyStyle.js'

const props = defineProps({
  // A GeoJSON FeatureCollection from the lookup endpoint: one `constituency`
  // feature per candidate (carrying its label, number and label anchor) plus the
  // settlement's own outline.
  geojson: { type: Object, required: true },
  // { tile_url, tile_attribution, max_zoom } — the basemap, configured server-side
  // so the attribution the tile provider requires always travels with the URL.
  map: { type: Object, required: true },
  // `evk` of the currently chosen constituency, or null while none is picked.
  selected: { type: String, default: null },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const host = ref(null)
const failed = ref(false)
// Leaflet objects are deliberately NOT reactive: they are large, self-mutating and
// cyclic, and making them reactive both wastes work and can break the library.
const leaflet = shallowRef(null)
const mapInstance = shallowRef(null)
const layersByEvk = shallowRef(new Map())
const labelsByEvk = shallowRef(new Map())

// The regions get their own map pane so they can be clipped as a group, below the
// default overlay pane (400) where the settlement outline is drawn — so the
// outline itself is never clipped by its own shape.
const REGION_PANE = 'evkRegions'
const REGION_PANE_Z = 390
// Unique per mounted map, so two of them on one page can't share a clip path.
const clipId = `pm-evk-clip-${Math.random().toString(36).slice(2, 9)}`

const parts = computed(() =>
  (props.geojson.features || []).filter((f) => f.properties?.kind === 'constituency'))

function styleFor(feature) {
  const evk = feature.properties?.evk
  const active = props.selected != null && props.selected === evk
  return {
    // One wash for every region — nothing is encoded in colour here (see
    // constituencyStyle.js). What tells them apart is the number on each and the
    // boundary between them; selection is a heavier outline and a stronger fill,
    // never a different hue.
    //
    // The seam is a dark hairline, not the usual surface-coloured gap: over a pale
    // street map a white seam disappears, and the boundary between two
    // constituencies is the one line on this map the reader must see.
    color: active ? '#3e3b32' : CONSTITUENCY_INK,
    weight: active ? 4 : 1.5,
    opacity: active ? 1 : 0.85,
    fillColor: CONSTITUENCY_FILL,
    fillOpacity: active ? FILL_OPACITY_SELECTED : FILL_OPACITY,
  }
}

function restyle() {
  const L = leaflet.value
  if (!L) return
  for (const [evk, layer] of layersByEvk.value) {
    layer.setStyle(styleFor({ properties: { evk } }))
  }
  // The number pills carry the selection too. With every region the same colour
  // they are the primary identifier, so the chosen one has to read as chosen from
  // the label alone — not only from the fill under it.
  //
  // The element is looked up per call, not cached: Leaflet defers a tooltip's
  // creation until the map has a view, so at build time `getElement()` is still
  // null and a cached reference would be null forever.
  for (const [evk, tip] of labelsByEvk.value) {
    const el = tip.getElement()
    if (el) el.classList.toggle('is-selected', props.selected === evk)
  }
}
watch(() => props.selected, restyle)

async function build() {
  let L
  try {
    // The stylesheet must come with it — Leaflet's panes are positioned by CSS,
    // and without it the tiles pile up in the top-left corner.
    await import('leaflet/dist/leaflet.css')
    L = (await import('leaflet')).default
  } catch {
    failed.value = true
    return
  }
  if (!host.value) return
  leaflet.value = L

  const map = L.map(host.value, {
    scrollWheelZoom: true,
    zoomControl: true,
    attributionControl: true,
  })
  mapInstance.value = map
  map.createPane(REGION_PANE).style.zIndex = String(REGION_PANE_Z)
  L.tileLayer(props.map.tile_url, {
    maxZoom: props.map.max_zoom || 18,
    attribution: props.map.tile_attribution || '',
  }).addTo(map)

  const byEvk = new Map()
  const labels = new Map()
  for (const feature of parts.value) {
    const { evk, label, official_name: officialName, label_point: labelPoint } =
      feature.properties
    const layer = L.geoJSON(feature, { pane: REGION_PANE, style: () => styleFor(feature) })
    layer.addTo(map)
    layer.on('click', () => emit('select', evk))
    // Keyboard reachability: Leaflet gives an interactive path a focusable DOM
    // node, so name it and make Enter/Space act like a click (A11Y-1).
    layer.eachLayer((sub) => {
      const el = sub.getElement && sub.getElement()
      if (!el) return
      el.setAttribute('tabindex', '0')
      el.setAttribute('role', 'button')
      el.setAttribute('aria-label', officialName || label)
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          emit('select', evk)
        }
      })
    })
    // The region's number, permanently on the map. With every region the same
    // colour this is *the* identifier, not a decoration (A11Y-1). Anchored to the
    // point the server picked inside the part of the settlement this constituency
    // covers — Leaflet's default would place it at the polygon's centroid, which
    // for these large shapes is usually off screen.
    if (labelPoint) {
      const tip = L.tooltip({ permanent: true, direction: 'center',
                              className: 'evk-label', interactive: true })
        .setLatLng([labelPoint[1], labelPoint[0]])
        .setContent(String(feature.properties.number))
        .addTo(map)
      tip.on('click', () => emit('select', evk))
      tip.on('add', () => {
        const el = tip.getElement()
        if (el) el.title = officialName || label
      })
      labels.set(evk, tip)
    }
    byEvk.set(evk, layer)
  }
  layersByEvk.value = byEvk
  labelsByEvk.value = labels

  // The settlement's own boundary, on top: it says which shape is "your town".
  const outline = (props.geojson.features || [])
    .find((f) => f.properties?.kind === 'settlement')
  if (outline) {
    const ring = L.geoJSON(outline, {
      style: { color: '#3e3b32', weight: 2.5, dashArray: '6 4', fill: false },
      interactive: false,
    }).addTo(map)
    // Fit to the settlement, which is also the extent of the drawn regions.
    map.fitBounds(ring.getBounds(), { padding: [12, 12] })
    // A constituency reaches far beyond the settlement — several districts, or half
    // a county — so drawn whole it sprawls across the map and invites the reader to
    // pick a region by a shape that is mostly somewhere else. Clip the regions to
    // the settlement: what stays on screen is exactly the question being asked,
    // "which part of *here* do you live in?", over an unobscured basemap.
    //
    // Deferred to `whenReady`: Leaflet holds back every layer's `onAdd` until the
    // map has a view, so until the `fitBounds` above there is no renderer <svg> in
    // the region pane to attach a clip to.
    map.whenReady(() => {
      clipRegionsTo(L, map, outline.geometry.coordinates[0])
      restyle()   // the number pills exist only now; mark the selected one
    })
  } else {
    const all = L.featureGroup([...byEvk.values()])
    map.fitBounds(all.getBounds(), { padding: [12, 12] })
  }
}

// Clip everything in the region pane to the settlement's outline.
//
// Leaflet's SVG renderer gives each pane its own <svg> whose viewBox origin IS the
// layer-point origin, so a clip path written in layer points lines up exactly with
// the polygon paths' own coordinates. Panning only translates the map pane and
// zooming rescales it, and the clip path rides along inside the same <svg>; only a
// zoom (which redefines layer points) needs the path recomputed.
//
// Uses public Leaflet API plus plain DOM — no renderer internals. If the pane has
// no <svg> (a Canvas renderer, say), clipping is skipped and the regions simply
// draw unclipped, as they did before.
function clipRegionsTo(L, map, ring) {
  const pane = map.getPane(REGION_PANE)
  const svg = pane && pane.querySelector('svg')
  const group = svg && svg.querySelector('g')
  if (!group) return

  const svgNS = 'http://www.w3.org/2000/svg'
  const defs = document.createElementNS(svgNS, 'defs')
  const clip = document.createElementNS(svgNS, 'clipPath')
  clip.setAttribute('id', clipId)
  clip.setAttribute('clipPathUnits', 'userSpaceOnUse')
  const path = document.createElementNS(svgNS, 'path')
  clip.appendChild(path)
  defs.appendChild(clip)
  svg.appendChild(defs)
  group.setAttribute('clip-path', `url(#${clipId})`)

  const latLngs = ring.map(([lon, lat]) => L.latLng(lat, lon))
  const redraw = () => {
    const d = latLngs
      .map((ll, i) => {
        const p = map.latLngToLayerPoint(ll)
        return `${i ? 'L' : 'M'}${p.x} ${p.y}`
      })
      .join(' ')
    path.setAttribute('d', `${d} Z`)
  }
  redraw()
  // `zoomend` covers the only case that changes layer-point values; `viewreset`
  // covers the non-animated jumps (setView, a resize) that also rebuild them.
  map.on('zoomend viewreset', redraw)
}

onMounted(build)
onBeforeUnmount(() => {
  if (mapInstance.value) mapInstance.value.remove()
  mapInstance.value = null
})
</script>

<template>
  <div class="mapwrap">
    <p v-if="failed" class="state" role="alert">{{ $t('lookup.mapFailed') }}</p>
    <div
      v-else ref="host" class="evkmap"
      :aria-label="ariaLabel || $t('lookup.mapLabel')" role="application"
    ></div>
  </div>
</template>

<style scoped>
.mapwrap { margin: .8rem 0; }
.evkmap {
  /* Kept near 3:2 rather than spanning the full page width. `fitBounds` picks the
     largest zoom at which the settlement fits BOTH dimensions, so a very wide box
     zooms out until a compact settlement fits the height — and the reader ends up
     looking at the whole county with their district a small patch in the middle.
     Capping the width keeps the settlement filling the frame it's fitted to. */
  width: 100%; max-width: 820px; height: 480px;
  border-radius: var(--radius); border: 1px solid var(--line);
  /* Leaflet paints its own tile pane; a background keeps the box from flashing
     white-on-white while the first tiles load. */
  background: var(--bg);
}
@media (max-width: 560px) { .evkmap { height: 340px; } }
</style>

<style>
/* Not scoped: Leaflet appends its tooltips to the map pane, outside this
   component's DOM scope. A pill on the surface colour rather than text on the
   fill, so the number stays legible over any region colour or basemap detail. */
.leaflet-tooltip.evk-label {
  display: flex; align-items: center; justify-content: center;
  box-sizing: content-box; min-width: 1.05rem; height: 1.05rem;
  background: var(--surface); color: var(--ink); border: 1px solid #b9bdc4;
  border-radius: 999px; box-shadow: var(--shadow); cursor: pointer;
  font-weight: 700; font-size: .84rem; padding: .18rem .4rem; white-space: nowrap;
}
.leaflet-tooltip.evk-label::before { display: none; }  /* no callout arrow */
/* The chosen region's number, inverted — with all the regions one colour, the pill
   has to say which is selected on its own. */
.leaflet-tooltip.evk-label.is-selected {
  background: #3e3b32; color: #fff; border-color: #3e3b32;
}
</style>
