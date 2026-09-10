<script setup>
// Force-directed SVG graph of who interjects over whose speech (§6E/INT-5).
// `nodes` = [{ person_id, label, faction, out, in }]; `links` = [{ source, target,
// count }] referencing node indices.
//
// **Why the position means something.** The first version of this figure put the
// members on a ring in a fixed order, which is honest but says nothing: every
// distance on it is an artefact of the sort. Here the layout is the finding —
// members who shout at each other are pulled together, so the picture separates
// into the knots of people who actually argue with each other, and a member who
// heckles widely sits apart from one locked into a single duel. Faction stays a
// colour rather than becoming a position, which is what lets the reader see
// whether the knots line up with the benches or cut across them.
//
// **It is still deterministic**, which a force layout usually is not and which
// this figure needs — two cycle scopes have to be comparable, an embed (§4C) has
// to reproduce, and a screenshot has to be worth trusting. Two properties buy it:
// d3-force v3 seeds its own LCG rather than calling `Math.random`, and it places
// nodes initially on a phyllotaxis spiral by index. So the same nodes in the same
// order (the backend sorts them) settle in the same place every time. The
// simulation is therefore run to completion **synchronously, once**, and the
// result drawn — never animated, which would also make the first second of the
// page a wobbling mess.
//
// **Why arrows and not ribbons.** Who shouted at whom is the whole content of an
// edge, and A→B and B→A are routinely both present and very different in size. So
// each direction is its own curve, bowed consistently to the LEFT of its own
// direction of travel — which is what separates the pair — and tipped with an
// arrowhead at the member being interrupted. Thickness is the count.
//
// The view can be zoomed and panned (d3-zoom: wheel, pinch, drag, and buttons for
// anyone not using a pointer), and any member can be dragged out of the tangle.
// Each arrow and each node is keyboard-focusable and labelled for assistive tech,
// and the figure carries a text alternative, since a mesh of curves is not
// readable by a screen reader however its parts are labelled (A11Y-1).
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY }
  from 'd3-force'
import { select } from 'd3-selection'
import { zoom as d3zoom, zoomIdentity } from 'd3-zoom'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  links: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  showCaption: { type: Boolean, default: true },
  selected: { type: Number, default: -1 },       // index of the selected link
  selectedNode: { type: Number, default: -1 },   // index of the selected node
  // Labels for the zoom controls, so the component needs no i18n of its own.
  labels: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['select', 'select-node'])

// The design box the figure is laid out in. Its **height** follows the settled
// layout's own aspect (clamped) rather than being a constant: a force layout's
// outline is whatever the data made it, and a fixed frame either letterboxes a
// wide one or crops a tall one.
//
// Its width is a plain constant, and deliberately so: the box is only a unit
// system. Narrowing it would also narrow the fit scale by the same factor, so the
// apparent size of everything in it is exactly unchanged — legibility on a phone
// is bought by `labelScale` below, not here.
const BOX_W = 900
const BOX_ASPECT = [0.52, 1.15]     // height as a share of width, clamped
const FRAME_PAD = 26
const LABEL_PX = 12                 // .nlabel's font-size, in box units
const LABEL_TARGET_PX = 12.5        // ...and what it should come out as on screen
// Below this rendered width a full "Surname Given" costs 40% of the figure, and
// reserving room for two of them leaves the graph a speck in the middle. Hungarian
// writes the surname first, so the first token alone is the useful short form —
// and the full name stays in the tooltip and the accessible name.
const SURNAMES_BELOW = 560
const NODE_MIN = 4         // marker radius for the least involved person shown
const NODE_MAX = 15        // ...and for the most
const EDGE_MIN = 1         // stroke width for the thinnest arrow
const EDGE_MAX = 8
const BOW = 0.13           // how far a curve is pushed off its own straight line
const NAME_MAX = 20        // characters of a name kept before it is elided
const CHAR_W = 6.2         // rough advance width of the label font, for spacing
// Wide enough at the bottom that the initial fit of a big, sprawling layout is
// never already outside it (`zoom.transform` sets a scale verbatim; only later
// interaction is clamped, so a too-tight floor makes the first wheel jump).
const ZOOM_RANGE = [0.15, 10]
// Somebody with no faction in the cycle in view — a minister or state secretary
// holding no mandate, whom the register gives no membership and the rest of the
// site shows by office instead (REP-12). A neutral tone, deliberately not the
// brand accent: a red dot in a row of party colours reads as a party.
const NO_FACTION = 'var(--ink-faint)'

function trunc(s, n = NAME_MAX) {
  s = String(s == null ? '' : s)
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

// How a member is named on the chart itself. Everything that measures a label —
// the placement pass, the frame's margin — must ask this, not the raw label.
function chartName(label) {
  const full = String(label == null ? '' : label)
  return trunc(stageWidth.value < SURNAMES_BELOW ? (full.split(' ')[0] || full) : full)
}

const svgRef = ref(null)
const stageRef = ref(null)
const stageWidth = ref(BOX_W)   // the figure's rendered width, in CSS pixels
const WIDTH = BOX_W
const HEIGHT = ref(620)
// The settled layout: one entry per node, in the order the props gave them.
const placed = shallowRef([])
const view = ref(zoomIdentity)      // current pan/zoom, applied to the whole scene
const active = ref(-1)              // hovered/focused node index
let fitted = zoomIdentity           // the transform that framed the fresh layout
let zoomBehaviour = null

// --- layout ---------------------------------------------------------------

function computeLayout() {
  const nodes = props.nodes
  if (!nodes.length) { placed.value = []; return }

  const maxTotal = Math.max(...nodes.map((n) => (n.out || 0) + (n.in || 0)), 1)
  const sqrtScale = (v, max, lo, hi) =>
    lo + (hi - lo) * Math.sqrt(Math.max(v, 0) / max)

  const sim = nodes.map((n, i) => {
    const total = (n.out || 0) + (n.in || 0)
    const r = sqrtScale(total, maxTotal, NODE_MIN, NODE_MAX)
    return {
      index: i,
      r,
      // How much room this node claims. The label sits beside the marker, so the
      // space a name needs is what actually keeps the picture readable — a
      // collision radius of the dot alone spreads the dots and stacks the names.
      pad: r + 11 + Math.min(trunc(n.label).length * CHAR_W, 110) * 0.38,
    }
  })

  // The springs are undirected: A→B and B→A are one relationship pulling once,
  // with the two counts summed. Left as two parallel links the pair would pull
  // twice as hard as a one-way pair of the same size, which is not what the
  // picture should say.
  const merged = new Map()
  for (const l of props.links) {
    const a = Math.min(l.source, l.target)
    const b = Math.max(l.source, l.target)
    const key = `${a}-${b}`
    merged.set(key, (merged.get(key) || 0) + l.count)
  }
  const maxPair = Math.max(...merged.values(), 1)
  const springs = [...merged.entries()].map(([key, count]) => {
    const [a, b] = key.split('-').map(Number)
    // Weight on a square root again: the counts span two orders of magnitude, and
    // on a linear scale one duel would collapse to a point and everything else
    // would be a uniform haze at the same distance.
    const w = Math.sqrt(count / maxPair)
    return { source: a, target: b, w }
  })

  const simulation = forceSimulation(sim)
    .force('link', forceLink(springs)
      .id((d) => d.index)
      .distance((d) => 210 - 130 * d.w)
      .strength((d) => 0.05 + 0.55 * d.w))
    .force('charge', forceManyBody().strength(-520).distanceMax(760))
    .force('collide', forceCollide((d) => d.pad).iterations(3))
    // A weak pull to the middle keeps a member with a single thin arrow from
    // drifting off the canvas; it is deliberately weaker on x, where there is
    // more room and where the labels need it.
    .force('x', forceX(0).strength(0.035))
    .force('y', forceY(0).strength(0.06))
    .stop()

  // Run it out in one go rather than animating: d3's own "how many ticks until
  // alpha decays past the floor" formula, so the layout is the converged one.
  const ticks = Math.ceil(
    Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()))
  simulation.tick(ticks)

  placed.value = sim.map((d, i) => ({ index: i, r: d.r, pad: d.pad, x: d.x, y: d.y }))
  fitted = frameLayout(placed.value)
  view.value = fitted
  applyView(fitted)
}

// Size the frame to the settled layout and centre it inside. The framing is a zoom
// transform rather than a rewritten viewBox so that "reset view" and every later
// wheel/pinch compose against the same origin; the viewBox itself only ever gets
// the box's aspect, which is what keeps the figure from being letterboxed.
function frameLayout(points) {
  if (!points.length) { HEIGHT.value = Math.round(WIDTH * 0.7); return zoomIdentity }
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  let widest = 0
  points.forEach((p, i) => {
    minX = Math.min(minX, p.x - p.r)
    maxX = Math.max(maxX, p.x + p.r)
    minY = Math.min(minY, p.y - p.r)
    maxY = Math.max(maxY, p.y + p.r)
    widest = Math.max(widest, chartName(props.nodes[i]?.label).length + 5)
  })
  const spanX = Math.max(maxX - minX, 1)
  const spanY = Math.max(maxY - minY, 1)

  // A name is drawn at a fixed size **on screen** (see `labelScale`), so the room
  // it needs is a fixed number of CSS pixels — which does not shrink as the cloud
  // is scaled down, and so cannot be folded into the bounding box the way the node
  // markers can. It is reserved as a margin instead, converted into box units.
  const marginPx = widest * CHAR_W * (LABEL_TARGET_PX / LABEL_PX)
  const margin = marginPx * (WIDTH / Math.max(stageWidth.value, 1))
  const usableW = Math.max(WIDTH - 2 * FRAME_PAD - 2 * margin, WIDTH * 0.3)

  // Scale to the width first, then make the frame exactly as tall as what that
  // produced (within the clamp). Choosing the height first and scaling into it is
  // what left a small cloud floating in a tall empty box.
  const w = WIDTH
  const k = Math.min(usableW / spanX, (w * BOX_ASPECT[1]) / spanY, 2.2)
  const wanted = k * spanY + 2 * FRAME_PAD + 2 * LABEL_TARGET_PX * (WIDTH / stageWidth.value)
  const h = Math.round(Math.min(Math.max(wanted, w * BOX_ASPECT[0]), w * BOX_ASPECT[1]))
  HEIGHT.value = h
  return zoomIdentity
    .translate(w / 2 - k * (minX + maxX) / 2, h / 2 - k * (minY + maxY) / 2)
    .scale(k)
}

// --- what gets drawn ------------------------------------------------------

const points = computed(() => {
  const out = placed.value
  if (!out.length) return []
  return props.nodes.map((n, i) => {
    const p = out[i] || { x: 0, y: 0, r: NODE_MIN }
    return {
      ...n,
      i,
      x: p.x,
      y: p.y,
      r: p.r,
      total: (n.out || 0) + (n.in || 0),
      color: n.faction?.color || NO_FACTION,
      // The name goes on the side away from the middle of the picture, which is
      // where the space is.
      flip: p.x < 0,
    }
  })
})

const arrows = computed(() => {
  const pts = points.value
  if (!pts.length) return []
  const maxCount = Math.max(...props.links.map((l) => l.count), 1)
  const width = (c) => EDGE_MIN + (EDGE_MAX - EDGE_MIN) * Math.sqrt(c / maxCount)
  return props.links.map((l, k) => {
    const s = pts[l.source]
    const t = pts[l.target]
    if (!s || !t) return null
    const dx = t.x - s.x
    const dy = t.y - s.y
    const len = Math.hypot(dx, dy) || 1
    // Control point pushed along the LEFT normal of this arrow's own direction —
    // which is what puts A→B and B→A on opposite sides of the line between them
    // instead of exactly on top of each other.
    const cx = (s.x + t.x) / 2 + (-dy / len) * len * BOW
    const cy = (s.y + t.y) / 2 + (dx / len) * len * BOW
    const w = width(l.count)
    // Step off a marker's edge along the curve — which near either end means
    // along the straight line to the control point. Never step *past* that point
    // though: two members dragged together leave less room between them than
    // their own radii, and an endpoint on the far side of the control point
    // reverses the tangent and turns the arrow inside out.
    const towards = (from, back) => {
      const ux = cx - from.x
      const uy = cy - from.y
      const d = Math.hypot(ux, uy) || 1
      const off = Math.min(back, d * 0.9)
      return { x: from.x + (ux / d) * off, y: from.y + (uy / d) * off,
               ux: ux / d, uy: uy / d }
    }
    const tail = towards(s, s.r + 1)
    // The apex lands just off the target's marker, because the arrow has to point
    // AT the member being interrupted. Backing it off by the head's own size
    // instead left the thickest arrows — the ones the reader looks at first —
    // floating a marker's width clear of their target.
    const tip = towards(t, t.r + 1.5)
    // The arrowhead follows the curve's tangent at its end, which for a quadratic
    // Bézier is just the direction from the control point to that end.
    const hx = -tip.ux
    const hy = -tip.uy
    // Longer than it is wide, and sized off the line it terminates. A head as
    // broad as it is long reads as a lozenge stuck onto the curve rather than as
    // a point; one that ignores the line's weight looks bolted on.
    const run = Math.hypot(tip.x - tail.x, tip.y - tail.y) || 1
    // ...but never so long that it eats its own arrow: two members dragged
    // together (or settled side by side) leave a very short run, and a head
    // measured only against the stroke would then overshoot the tail and draw
    // backwards.
    const fit = Math.min(1, (run * 0.45) / (3.5 + w * 2.6))
    const headLen = (3.5 + w * 2.6) * fit
    const headHalf = (1.2 + w * 0.9) * fit
    // Where the head begins is also where the stroke has to stop. Drawing the
    // curve on underneath it would show through, since both are translucent, as
    // a darker bar down the middle of every point.
    const base = { x: tip.x - hx * headLen, y: tip.y - hy * headLen }
    return {
      key: k,
      count: l.count,
      source: s,
      target: t,
      width: w,
      color: s.color,
      // The stroke stops at the head's base. `base` sits on the same ray from the
      // control point as `tip` does, so ending the curve there leaves its tangent
      // unchanged and the butt cap comes out flush with the head's base edge —
      // the pair still reads as one continuous arrow.
      d: `M${tail.x.toFixed(1)},${tail.y.toFixed(1)} `
         + `Q${cx.toFixed(1)},${cy.toFixed(1)} ${base.x.toFixed(1)},${base.y.toFixed(1)}`,
      head: `M${tip.x.toFixed(1)},${tip.y.toFixed(1)} `
            + `L${(base.x - hy * headHalf).toFixed(1)},${(base.y + hx * headHalf).toFixed(1)} `
            + `L${(base.x + hy * headHalf).toFixed(1)},${(base.y - hx * headHalf).toFixed(1)} Z`,
    }
  }).filter(Boolean)
})

// The figure's text alternative: a mesh of curves cannot be read by a screen
// reader whatever each path is labelled, so the strongest arrows are also stated
// in words (A11Y-1).
const summary = computed(() => arrows.value
  .slice()
  .sort((a, b) => b.count - a.count)
  .slice(0, 8)
  .map((a) => `${a.source.label} → ${a.target.label}: ${a.count}`)
  .join('; '))

// How many CSS pixels one box unit currently occupies — the viewBox's own scaling
// times the reader's zoom. This is the number that decides whether anything is
// legible, and it is why the box width above cannot help: it cancels out of it.
const pxPerUnit = computed(() => (stageWidth.value / WIDTH) * view.value.k)

// Names are drawn at a **constant size on screen**, not a constant size in the
// layout: a name is either readable or it is not, and that is a fact about the
// reader's screen. So the label is scaled inversely to the current pixel density
// of the figure — which on a wide screen is barely any correction, and on a phone
// (or zoomed right out) is a large one. It comes back out in the layout as names
// that take *more room* there, and the placement pass answers that by dropping the
// ones it cannot fit — five readable names beat ten illegible ones, and zooming in
// brings the rest back.
const labelScale = computed(() =>
  Math.min(Math.max(LABEL_TARGET_PX / (LABEL_PX * pxPerUnit.value), 0.55), 3.6))

// --- placing the names ----------------------------------------------------
//
// A collision force can only reserve a circle, and a name is a long thin box on
// one side of its dot — so spacing the dots enough to space the names would blow
// the layout apart and shrink the type to nothing. The names are therefore placed
// *after* the layout, greedily: the most-involved member picks first, each one
// takes the first free slot among (preferred side, other side) × (no offset, one
// line up, one line down, two up, two down), and a name that finds no free slot
// is left off rather than printed over another.
//
// It is keyed on `labelScale`, which is what makes the omission temporary: zooming
// in shrinks a name *in graph units*, slots open up, and the missing names appear.
// Every node keeps its tooltip and its accessible name either way.
const LINE_H = 15

function overlaps(a, b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1
}

const labelBoxes = computed(() => {
  const pts = points.value
  const scale = labelScale.value
  const placedBoxes = pts.map((p) => ({
    x0: p.x - p.r, x1: p.x + p.r, y0: p.y - p.r, y1: p.y + p.r,
  }))
  const chosen = new Map()
  // Most involved first: where two names cannot both fit, the bigger number is
  // the one the reader is more likely to be looking for.
  const order = [...pts].sort((a, b) => b.total - a.total || a.i - b.i)
  for (const p of order) {
    const width = (chartName(p.label).length + String(p.total).length + 2) * CHAR_W * scale
    const height = LINE_H * scale
    const sides = p.flip ? [-1, 1] : [1, -1]
    let picked = null
    for (const dy of [0, -LINE_H * scale, LINE_H * scale,
                      -2 * LINE_H * scale, 2 * LINE_H * scale]) {
      for (const side of sides) {
        const gap = p.r + 6 * scale
        const x0 = side > 0 ? p.x + gap : p.x - gap - width
        const box = { x0, x1: x0 + width,
                      y0: p.y + dy - height / 2, y1: p.y + dy + height / 2 }
        if (!placedBoxes.some((b) => overlaps(b, box))) { picked = { side, dy, box }; break }
      }
      if (picked) break
    }
    if (picked) {
      placedBoxes.push(picked.box)
      chosen.set(p.i, { side: picked.side, dy: picked.dy })
    }
  }
  return chosen
})

// A dense graph is drawn fainter. Forty members carry seven times the arrows
// twelve do, and at one fixed opacity the mesh turns into a solid wash where the
// individual arrow — the thing that is clickable and that carries the number —
// stops being visible at all. Hovering and selecting still bring one up to full.
const restOpacity = computed(() => {
  const n = props.links.length || 1
  return Math.max(0.13, Math.min(0.5, 0.5 * Math.sqrt(120 / n)))
})

// --- the hovered member's card --------------------------------------------
//
// A dot on a force layout is anonymous: the position is an argument about who
// argues with whom, not a label, and the name beside it is dropped whenever the
// placement pass cannot fit it. So the card carries the identity — face, name,
// faction — and then the two numbers the figure is actually about, told apart in
// words rather than left as the old `905 / 12`, which said nothing about which
// was which.
//
// It is HTML in the stage rather than an SVG `<title>`, because a `<title>` is
// the browser's own tooltip: unstyled, on its own delay, one line, and no place
// for a photo. Screen readers get the same facts from each node's `aria-label`,
// so the card is hidden from them instead of read out twice.
const tip = computed(() => {
  const p = points.value[active.value]
  if (!p) return null
  // Graph units → CSS pixels: the reader's pan/zoom, then the viewBox's own
  // scaling (uniform, since the svg is `width:100%; height:auto`).
  const s = stageWidth.value / WIDTH
  const x = (view.value.x + view.value.k * p.x) * s
  const y = (view.value.y + view.value.k * p.y) * s
  const r = p.r * view.value.k * s
  // The card grows towards the middle of the figure, so it cannot run off the
  // edge its own member sits nearest — and the most-involved members sit out at
  // the edges. Anchoring by the near corner does this without measuring the
  // text, which is what a centred card would need.
  const toLeft = x > stageWidth.value / 2
  const below = y < HEIGHT.value * s * 0.42
  return {
    node: p,
    x: toLeft ? x - r - 10 : x + r + 10,
    y: below ? y + r + 8 : y - r - 8,
    shift: `translate(${toLeft ? '-100%' : '0'}, ${below ? '0' : '-100%'})`,
  }
})

// A member with no photo simply gets none: the tooltip reads perfectly well
// without one, and a broken image in it would not.
function onPhotoError(event) { event.target.hidden = true }

// The card's facts as one string, for the screen reader that cannot see it.
function nodeAria(p) {
  const parts = [p.label]
  if (p.faction?.label) parts.push(p.faction.label)
  parts.push(`${props.labels.made}: ${p.out}`, `${props.labels.received}: ${p.in}`)
  return parts.join(', ')
}

// --- zoom, pan and dragging a member out of the tangle --------------------

function applyView(transform) {
  if (!svgRef.value || !zoomBehaviour) return
  select(svgRef.value).call(zoomBehaviour.transform, transform)
}

function zoomBy(factor) {
  if (!svgRef.value || !zoomBehaviour) return
  select(svgRef.value).call(zoomBehaviour.scaleBy, factor)
}

function resetView() { applyView(fitted) }

// Track the figure's rendered width — a rotated phone, a resized window, a sidebar
// opening — because it is what the on-screen size of a name is measured against.
let observer = null

function watchWidth() {
  if (typeof ResizeObserver === 'undefined' || !stageRef.value) return
  observer = new ResizeObserver(([entry]) => {
    const w = entry.contentRect.width
    if (!w || Math.abs(w - stageWidth.value) < 1) return
    stageWidth.value = w
    // Only re-frame while the reader has not taken over the view themselves.
    if (view.value === fitted) { fitted = frameLayout(placed.value); applyView(fitted) }
  })
  observer.observe(stageRef.value)
}

onMounted(() => {
  stageWidth.value = stageRef.value?.clientWidth || BOX_W
  computeLayout()
  zoomBehaviour = d3zoom()
    .scaleExtent(ZOOM_RANGE)
    // d3's own default filter, spelled out: a trackpad pinch arrives as a
    // ctrl+wheel and must still zoom, and only the primary button pans. A press
    // that began on a member never reaches here — that handler stops it.
    .filter((event) => (!event.ctrlKey || event.type === 'wheel') && !event.button)
    .on('zoom', (event) => { view.value = event.transform })
  select(svgRef.value).call(zoomBehaviour)
  applyView(fitted)
  watchWidth()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  if (svgRef.value && zoomBehaviour) select(svgRef.value).on('.zoom', null)
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
})

watch(() => props.nodes, computeLayout)

// Dragging a member pins them where they are dropped: the settled layout is a
// good starting point, not an argument, and untangling one name by hand is the
// cheapest way to read a knot. "Reset view" puts everyone back.
//
// Bound to `mousedown` rather than `pointerdown`, because that is the event
// d3-zoom listens to for panning: stopping the pointer event would leave the
// compatibility mouse event to pan the canvas underneath the member being
// dragged. Touch is deliberately left to the browser and to d3 — with
// `touch-action: pan-y` a one-finger drag scrolls the page (a chart in the middle
// of an article must not trap the reader), two fingers pinch, and a tap selects.
let dragging = null

function graphPoint(event) {
  const rect = svgRef.value.getBoundingClientRect()
  const sx = (event.clientX - rect.left) * (WIDTH / rect.width)
  const sy = (event.clientY - rect.top) * (HEIGHT.value / rect.height)
  return view.value.invert([sx, sy])
}

function onNodeDown(event, i) {
  if (event.button) return
  const [x, y] = graphPoint(event)
  const p = placed.value[i]
  dragging = { i, dx: p.x - x, dy: p.y - y, moved: false }
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
}

function onDragMove(event) {
  if (!dragging) return
  const [x, y] = graphPoint(event)
  const p = placed.value[dragging.i]
  const nx = x + dragging.dx
  const ny = y + dragging.dy
  if (Math.hypot(nx - p.x, ny - p.y) > 1) dragging.moved = true
  p.x = nx
  p.y = ny
  placed.value = [...placed.value]     // shallowRef: hand Vue a new array
}

function onDragEnd() {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
  // Leave `moved` readable by the click that follows this mouseup, then forget.
  const moved = dragging?.moved
  dragging = null
  suppressClick = !!moved
}

// A drag ends in a `click` on the member that was dragged; that click must not
// also open their list.
let suppressClick = false

function onNodeClick(i) {
  if (suppressClick) { suppressClick = false; return }
  pickNode(i)
}

// --- selection ------------------------------------------------------------

function pick(index) {
  const l = props.links[index]
  if (!l) return
  emit('select', { index, count: l.count,
                   source: props.nodes[l.source], target: props.nodes[l.target] })
}

function pickNode(index) {
  const n = props.nodes[index]
  if (!n) return
  emit('select-node', { index, node: n })
}

// An arrow is dimmed when something else is selected or hovered and it is not
// part of it — the same three-state rule the Sankey uses, so the two figures
// behave alike.
function dimmed(a) {
  if (props.selected !== -1) return a.key !== props.selected
  if (props.selectedNode !== -1) {
    return a.source.i !== props.selectedNode && a.target.i !== props.selectedNode
  }
  return active.value !== -1
    && a.source.i !== active.value && a.target.i !== active.value
}

function chosen(a) {
  return a.key === props.selected
    || (props.selectedNode !== -1
        && (a.source.i === props.selectedNode || a.target.i === props.selectedNode))
}
</script>

<template>
  <figure class="igraph" style="margin:0;">
    <figcaption v-if="caption && showCaption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div class="stage" ref="stageRef">
      <svg
        ref="svgRef" :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" class="svg"
        role="img" :aria-label="caption"
        :style="{ '--arrow-rest': restOpacity }"
      >
        <desc>{{ summary }}</desc>
        <g :transform="`translate(${view.x} ${view.y}) scale(${view.k})`">
          <!-- arrows: one per ordered pair, thickness = how many interjections -->
          <g
            v-for="a in arrows" :key="'a' + a.key"
            class="arrow" :class="{ dim: dimmed(a), sel: chosen(a) }"
            role="button" tabindex="0"
            :aria-label="`${a.source.label} → ${a.target.label}: ${a.count}`"
            @mousedown.stop @click="pick(a.key)"
            @keydown.enter.prevent="pick(a.key)" @keydown.space.prevent="pick(a.key)"
          >
            <title>{{ a.source.label }} → {{ a.target.label }}: {{ a.count }}</title>
            <!-- a wide, invisible copy of the curve, so a hairline arrow is still
                 something a mouse and a finger can hit -->
            <path :d="a.d" class="hit" />
            <path :d="a.d" class="line" :stroke="a.color" :stroke-width="a.width" />
            <path :d="a.head" class="head" :fill="a.color" />
          </g>

          <!-- people: one marker + name per node -->
          <g
            v-for="p in points" :key="'n' + p.i"
            class="nodegrp" :class="{ selnode: p.i === selectedNode }"
            role="button" tabindex="0"
            :aria-label="nodeAria(p)"
            @mouseenter="active = p.i" @mouseleave="active = -1"
            @focus="active = p.i" @blur="active = -1"
            @mousedown.stop="onNodeDown($event, p.i)"
            @click="onNodeClick(p.i)"
            @keydown.enter.prevent="pickNode(p.i)" @keydown.space.prevent="pickNode(p.i)"
          >
            <!-- No `<title>` here: the hover card below says all of this, and a
                 `<title>` alongside it would only add the browser's own second
                 tooltip on top of it. -->
            <!-- The selection ring is drawn OUTSIDE the marker, not as a stroke on
                 it: a thick stroke on a 4px dot swallows the fill, and the fill is
                 the faction. -->
            <circle
              v-if="p.i === selectedNode" :cx="p.x" :cy="p.y" :r="p.r + 4"
              class="ring"
            />
            <circle :cx="p.x" :cy="p.y" :r="p.r" class="dot" :fill="p.color" />
            <text
              v-if="labelBoxes.get(p.i) || p.i === selectedNode || p.i === active"
              class="nlabel"
              :text-anchor="(labelBoxes.get(p.i)?.side ?? (p.flip ? -1 : 1)) > 0 ? 'start' : 'end'"
              :transform="`translate(${p.x + ((labelBoxes.get(p.i)?.side ?? (p.flip ? -1 : 1)) > 0
                              ? p.r + 6 : -p.r - 6)} ${p.y + (labelBoxes.get(p.i)?.dy || 0)})`
                + ` scale(${labelScale})`"
              dominant-baseline="middle"
            >{{ chartName(p.label) }} <tspan class="nval">{{ p.total }}</tspan></text>
          </g>
        </g>
      </svg>

      <!-- The hovered (or keyboard-focused) member's card. `aria-hidden`, because
           the node it belongs to already carries the same words. -->
      <div
        v-if="tip" class="ntip" aria-hidden="true"
        :style="{ left: `${tip.x}px`, top: `${tip.y}px`, transform: tip.shift }"
      >
        <div class="ntip-head">
          <img
            v-if="tip.node.photo_uri" class="avatar sm" :src="tip.node.photo_uri"
            alt="" loading="lazy" @error="onPhotoError"
          />
          <div class="ntip-id">
            <span class="ntip-name">{{ tip.node.label }}</span>
            <span v-if="tip.node.faction?.label" class="ntip-faction">
              <i class="swatch" :style="{ background: tip.node.color }"></i>
              {{ tip.node.faction.label }}
            </span>
          </div>
        </div>
        <dl class="ntip-stats">
          <dt>{{ labels.made }}</dt>
          <dd>{{ tip.node.out }}</dd>
          <dt>{{ labels.received }}</dt>
          <dd>{{ tip.node.in }}</dd>
        </dl>
      </div>

      <!-- Zoom is otherwise a wheel/pinch gesture only, which is no control at
           all for a keyboard or a trackpad the reader would rather scroll with. -->
      <div class="zoomctl">
        <button type="button" class="zbtn" :aria-label="labels.zoomIn || '+'"
                @click="zoomBy(1.4)">+</button>
        <button type="button" class="zbtn" :aria-label="labels.zoomOut || '−'"
                @click="zoomBy(1 / 1.4)">−</button>
        <button type="button" class="zbtn reset" :aria-label="labels.reset || 'reset'"
                @click="resetView">⤢</button>
      </div>
    </div>
  </figure>
</template>

<style scoped>
.stage { position: relative; }
.svg {
  width: 100%; height: auto; display: block;
  /* One finger scrolls the page — a figure in the middle of an article must not
     trap the reader — while two still pinch-zoom the graph. */
  touch-action: pan-y;
  cursor: grab; background: transparent;
}
.svg:active { cursor: grabbing; }
/* The opacity belongs to the arrow as a whole, not to each of its two paths.
   Translucent siblings composite where they touch, so a per-path opacity turned
   every join between a stroke and its own head into a darker bar down the middle
   of the point; setting it on the group flattens the pair first, and only genuine
   crossings between *different* arrows still darken. */
.arrow { cursor: pointer; opacity: var(--arrow-rest, .46); transition: opacity .12s ease; }
.arrow .line { fill: none; stroke-linecap: butt; }
.hit { fill: none; stroke: transparent; stroke-width: 12; }
.arrow:hover { opacity: .92; }
.arrow.dim { opacity: .05; }
.arrow.sel { opacity: 1; }
.arrow:focus { outline: none; }
.arrow:focus-visible .line { stroke: var(--accent); }
.arrow:focus-visible .head { fill: var(--accent); }
.dot { stroke: var(--surface); stroke-width: 1.5; transition: stroke .12s ease; }
.nodegrp { cursor: grab; }
.nodegrp:active { cursor: grabbing; }
.nodegrp:focus { outline: none; }
.nodegrp:hover .dot, .nodegrp:focus-visible .dot { stroke: var(--ink-soft); }
.ring { fill: none; stroke: var(--accent); stroke-width: 2; }
.nodegrp.selnode .nlabel { font-weight: 700; }
/* Names sit over the arrows, so they carry a background halo to stay legible —
   the same trick the Sankey's interior labels use. */
.nlabel {
  font-size: 12px; fill: var(--ink); paint-order: stroke;
  stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round;
  pointer-events: none;
}
.nval { fill: var(--ink-faint); font-variant-numeric: tabular-nums; }

/* The hover card. Dark ink, small type, no pointer events — the same tooltip the
   trend chart uses, so the site has one tooltip rather than one per figure. It
   must not intercept the pointer: it sits right beside its own member, and a card
   that could be hovered would take the hover that is keeping it open. */
.ntip {
  position: absolute; z-index: 3; pointer-events: none;
  min-width: 9rem; max-width: 15rem;
  padding: .45rem .55rem; border-radius: 8px;
  background: var(--ink); color: #fff;
  font-size: .72rem; line-height: 1.3;
  box-shadow: 0 4px 14px rgba(0, 0, 0, .22);
}
.ntip-head { display: flex; align-items: center; gap: .45rem; }
/* The border the shared avatar carries is a light hairline, drawn for a light
   card; on this one it would ring the face. */
.ntip .avatar { border-color: rgba(255, 255, 255, .22); }
.ntip-id { display: flex; flex-direction: column; gap: .05rem; min-width: 0; }
.ntip-name { font-weight: 700; font-size: .78rem; }
.ntip-faction { display: flex; align-items: center; gap: .3rem; opacity: .72; }
.ntip-faction .swatch { width: .5rem; height: .5rem; border-radius: 50%; flex: none; }
.ntip-stats {
  display: grid; grid-template-columns: 1fr auto; gap: .05rem .6rem;
  margin: .4rem 0 0; padding-top: .35rem;
  border-top: 1px solid rgba(255, 255, 255, .18);
}
.ntip-stats dt { opacity: .72; }
.ntip-stats dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; text-align: right; }

.zoomctl {
  position: absolute; right: .5rem; top: .5rem;
  display: flex; flex-direction: column; gap: .25rem;
}
.zbtn {
  width: 1.9rem; height: 1.9rem; padding: 0; line-height: 1;
  font-size: 1rem; font-weight: 700; color: var(--ink-soft);
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  cursor: pointer;
}
.zbtn:hover { color: var(--accent); border-color: var(--accent); }
.zbtn.reset { font-size: .85rem; }
@media (prefers-reduced-motion: reduce) { .arrow, .dot { transition: none; } }
</style>
