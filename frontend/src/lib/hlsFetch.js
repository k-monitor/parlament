// Fetch a cropped HLS clip's raw MPEG-TS bytes for the exporter (VIE-10).
//
// The backend hands us a smil VOD *already cropped server-side* to the export
// window (VIE-9's per_speech_clip, generalised), so this only has to download
// that short window's segments — never the multi-hour day stream. The stream
// server sends `Access-Control-Allow-Origin: *` on both the playlist and the
// `.ts` segments, so the browser can fetch the raw bytes directly (no proxy).
//
// The segments are H.264 + MP3 in MPEG-TS containers, which concatenate cleanly
// (each is an independent packet stream), so the whole window is just the
// segments joined end to end — ready to hand to ffmpeg.wasm.

// The clip's smil VOD is generated on demand: its playlist 404s until the
// `playseq.php` activation endpoint has been hit once. Same cross-origin
// no-cors side-effect ping the player does (capped so a hang can't stall us).
function activate(playseq) {
  if (!playseq) return Promise.resolve()
  let f
  try { f = fetch(playseq, { mode: 'no-cors' }).catch(() => {}) }
  catch { return Promise.resolve() }
  return Promise.race([f, new Promise((r) => setTimeout(r, 4000))])
}

async function fetchText(url, signal) {
  const res = await fetch(url, { signal })
  if (!res.ok) throw new Error(`playlist ${res.status}`)
  return res.text()
}

// Parse a master playlist into its variant renditions. Returns [] for a media
// playlist (one with segments rather than #EXT-X-STREAM-INF variants).
function parseMaster(text, baseUrl) {
  const lines = text.split(/\r?\n/)
  const variants = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line.startsWith('#EXT-X-STREAM-INF')) continue
    const res = /RESOLUTION=(\d+)x(\d+)/.exec(line)
    const bw = /BANDWIDTH=(\d+)/.exec(line)
    // The URI is the next non-comment line.
    let uri = null
    for (let j = i + 1; j < lines.length; j++) {
      const l = lines[j].trim()
      if (l && !l.startsWith('#')) { uri = l; break }
    }
    if (!uri) continue
    variants.push({
      url: new URL(uri, baseUrl).href,
      height: res ? Number(res[2]) : 0,
      bandwidth: bw ? Number(bw[1]) : 0,
    })
  }
  return variants
}

// The segment URLs (resolved) of a media/chunklist playlist, in order.
function parseSegments(text, baseUrl) {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => new URL(l, baseUrl).href)
}

// Pick the variant whose height is closest to (but preferably at/under) the
// target; fall back to the single lowest when none is under it.
function pickVariant(variants, targetHeight) {
  if (variants.length === 0) return null
  const sorted = [...variants].sort((a, b) => a.height - b.height)
  const underOrEq = sorted.filter((v) => v.height <= targetHeight)
  return underOrEq.length ? underOrEq[underOrEq.length - 1] : sorted[0]
}

/**
 * Download the cropped clip as one concatenated MPEG-TS byte array.
 *
 * @param {object}   o
 * @param {string}   o.masterUrl       the clip's HLS playlist URL (from the backend)
 * @param {string?}  o.playseq         VOD activation URL (pinged once first)
 * @param {number}   o.targetHeight    desired vertical resolution (e.g. 480)
 * @param {AbortSignal?} o.signal       cancellation
 * @param {(p:{loaded:number,total:number})=>void} o.onProgress  segment progress
 * @returns {Promise<{data:Uint8Array, height:number}>}
 */
export async function fetchClipTs({ masterUrl, playseq, targetHeight = 480, signal, onProgress }) {
  await activate(playseq)

  // master -> chosen media playlist -> segments
  const masterText = await fetchText(masterUrl, signal)
  const variants = parseMaster(masterText, masterUrl)
  let mediaUrl = masterUrl
  let height = 0
  if (variants.length) {
    const chosen = pickVariant(variants, targetHeight)
    mediaUrl = chosen.url
    height = chosen.height
  }
  const mediaText = await fetchText(mediaUrl, signal)
  const segUrls = parseSegments(mediaText, mediaUrl)
  if (segUrls.length === 0) throw new Error('empty clip playlist')

  const chunks = []
  let bytes = 0
  for (let i = 0; i < segUrls.length; i++) {
    if (signal && signal.aborted) throw new DOMException('aborted', 'AbortError')
    const res = await fetch(segUrls[i], { signal })
    if (!res.ok) throw new Error(`segment ${i} ${res.status}`)
    const buf = new Uint8Array(await res.arrayBuffer())
    chunks.push(buf)
    bytes += buf.byteLength
    if (onProgress) onProgress({ loaded: i + 1, total: segUrls.length })
  }

  // Concatenate the segments into one contiguous TS byte array.
  const data = new Uint8Array(bytes)
  let off = 0
  for (const c of chunks) { data.set(c, off); off += c.byteLength }
  return { data, height }
}
