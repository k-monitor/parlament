// How the constituency picker draws its regions (REP-10).
//
// **Every region looks the same.** On a map of Hungarian politics no hue is free —
// the factions in this very database carry orange (Fidesz), blue (DK, KDNP), green
// (LMP, Mi Hazánk, SZDSZ), teal (TISZA), red (MSZP), brown (Jobbik), purple
// (Momentum, MIÉP) and grey (független) — and the MP's faction badge sits inches
// from the map. Giving each region its own colour therefore reads as a political
// claim about it, which is the opposite of what these polygons mean: a
// constituency is a piece of ground, and the reader is only being asked "which one
// do you live in?".
//
// So the regions share one neutral wash and are told apart by their **number** and
// the **boundary between them** — which is what a reader is looking for anyway.
// Nothing is encoded in colour at all, so there is nothing to misread, and the map
// survives greyscale printing and any colour-vision deficiency (A11Y-1).

// A desaturated slate: distinct from OpenStreetMap's greens, blues and beiges
// without being any party's colour.
export const CONSTITUENCY_INK = '#33414f'
export const CONSTITUENCY_FILL = '#5c6b7f'

// Light enough that the streets underneath stay readable — the basemap is what
// lets a reader recognise their own neighbourhood — but enough to bound the
// region. The chosen one is filled harder so the answer is obvious at a glance.
export const FILL_OPACITY = 0.2
export const FILL_OPACITY_SELECTED = 0.42
