// Client-side clip exporter engine (VIE-10) — ffmpeg.wasm.
//
// Everything runs in the browser: the (already public) MPEG-TS bytes are muxed
// into an MP4 with no server-side transcoding and no stored artefact, so the
// deployment pays nothing but the bytes the user already streamed.
//
// The single-thread @ffmpeg/core is used deliberately: the "none"/"soft" paths
// stream-COPY the H.264 video (fast, seconds), and a single-thread core needs no
// SharedArrayBuffer — so the app avoids the COOP/COEP cross-origin-isolation
// headers that would otherwise break the cross-origin parliament video and MP
// photos. Only "burned-in" re-encodes the video with libx264 (slower) — the UI
// warns before that path.
//
// Audio is ALWAYS transcoded to AAC (see the AAC constant below), never copied:
// the parliament source is MP3, and MP3-in-MP4 plays silently on iOS/Safari.
//
// The core is built WITHOUT fontconfig (verified in the wasm), so libass can't
// discover system fonts. For burn-in we therefore bundle a Hungarian-capable
// font (Liberation Sans) into the virtual FS and point the `subtitles` filter at
// it with `fontsdir` + a matching `force_style=FontName=...`.
import { FFmpeg } from '@ffmpeg/ffmpeg'
import { toBlobURL, fetchFile } from '@ffmpeg/util'
// The ESM core (Vite spawns a *module* worker, whose loader needs an ES module
// with a default export — the UMD core would import as `undefined`). Emitted as
// on-demand assets: only fetched when this (lazily-imported) module first runs.
import coreURL from '@ffmpeg/core?url'
import wasmURL from '@ffmpeg/core/wasm?url'
import fontURL from '../assets/subfont.ttf?url'
import watermarkURL from '../assets/watermark.png?url'

export const SUBTITLE_MODES = ['none', 'soft', 'burn']

let _ffmpeg = null      // shared instance (wasm load is ~31 MB — reuse it)
let _loadingPromise = null

// libass style for burned-in captions: white text, black outline, bottom-centre.
// ASS colours are &HAABBGGRR; FontName must match the bundled font's family.
// FontSize is in the SRT default 384×288 space (libass scales it up to the frame),
// kept modest so captions stay a compact 1–2 lines rather than filling the frame.
const BURN_STYLE =
  "FontName=Liberation Sans,FontSize=16,PrimaryColour=&H00FFFFFF," +
  "OutlineColour=&H00000000,BorderStyle=1,Outline=1.5,Shadow=0,MarginV=18,Alignment=2"

/** Has the user's browser got what ffmpeg.wasm needs? (WebAssembly + Worker) */
export function exportSupported() {
  return typeof WebAssembly === 'object' && typeof Worker === 'function'
}

/**
 * Load ffmpeg.wasm (once). `onProgress` receives 0..1 while the core downloads.
 */
export async function ensureLoaded({ onLog, onProgress } = {}) {
  if (_ffmpeg) return _ffmpeg
  if (_loadingPromise) return _loadingPromise
  _loadingPromise = (async () => {
    const ff = new FFmpeg()
    if (onLog) ff.on('log', ({ message }) => onLog(message))
    const [core, wasm] = await Promise.all([
      toBlobURL(coreURL, 'text/javascript'),
      toBlobURL(wasmURL, 'application/wasm', true, (e) => {
        // received/total, but @ffmpeg/util takes `total` from Content-Length —
        // the COMPRESSED size when the server sends the wasm gzip/br-encoded —
        // while `received` counts DECOMPRESSED stream bytes. The ratio then
        // blows past 1 (seen as >10000%). `total` is -1 with no Content-Length.
        // Clamp to 0..1 so callers always get a sane fraction.
        if (onProgress && e && e.total > 0) {
          onProgress(Math.max(0, Math.min(1, e.received / e.total)))
        }
      }),
    ])
    await ff.load({ coreURL: core, wasmURL: wasm })
    _ffmpeg = ff
    return ff
  })()
  try {
    return await _loadingPromise
  } finally {
    _loadingPromise = null
  }
}

/** Kill the running engine (cancellation). The next export reloads a fresh one. */
export function cancel() {
  if (_ffmpeg) {
    try { _ffmpeg.terminate() } catch { /* ignore */ }
    _ffmpeg = null
  }
}

const X264 = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p']

// The parliament HLS source carries MP3 audio, but Apple's AVFoundation (iOS
// Safari, and iOS apps like Discord that must use the system media stack) cannot
// decode MP3 inside an MP4/MOV container — it plays the video with no sound.
// (The same MP3 plays fine on iOS inside the original HLS/TS stream, which is why
// the live player works but a remuxed MP4 didn't.) So every path transcodes the
// audio to AAC — universally supported in MP4 — instead of stream-copying it.
// Audio-only transcode is cheap, so the "fast" video-copy paths stay fast.
const AAC = ['-c:a', 'aac', '-b:a', '160k']

// Center-crop the 16:9 source to a 9:16 portrait frame (for TikTok/Reels/Shorts):
// keep the full height, take the middle slice of the width. `trunc(.../2)*2` keeps
// the width even (yuv420p requires it). The speaker sits centre-frame, so the
// podium survives the crop.
const PORTRAIT_CROP = "crop='trunc(ih*9/16/2)*2':ih"

// Logo placement: scaled to a fraction of the (output) video width, pinned
// top-right with a small margin. The renditions are 16:9 (width ≈ height·16/9);
// a portrait crop narrows the frame to height·9/16, so the logo takes a bigger
// share of that width and the margin tracks it.
function watermarkGeom(height, portrait) {
  const h = height || 480
  const vidW = portrait ? Math.round((h * 9) / 16) : Math.round((h * 16) / 9)
  const frac = portrait ? 0.42 : 0.24
  return { logoW: Math.round(vidW * frac), margin: Math.max(8, Math.round(vidW * (portrait ? 0.03 : 0.02))) }
}

// Render the sitting-day date to a small transparent PNG "date bug" (a dark
// translucent chip + white text) that gets overlaid top-left alongside the
// watermark logo. Rasterising it in a canvas — rather than ffmpeg's drawtext —
// keeps the exporter independent of whether the wasm core ships the drawtext
// filter (it bundles libass for burn-in, but drawtext is a separate build flag),
// and lets the text use the browser's own fonts with no glyph-embedding dance.
async function makeDatePng(text, height, portrait) {
  const h = height || 480
  const vidW = portrait ? Math.round((h * 9) / 16) : Math.round((h * 16) / 9)
  const fontOf = (sz) => `700 ${sz}px "Segoe UI", system-ui, Roboto, Arial, sans-serif`
  const canvas = document.createElement('canvas')
  let ctx = canvas.getContext('2d')

  // The logo (top-right) leaves this much room on the left for the date chip.
  // In portrait the frame is narrow and the logo takes a bigger share of the
  // width (watermarkGeom), so cap the chip to whatever is left before the logo
  // (minus a gap) — otherwise the two labels overlap.
  const { logoW, margin } = watermarkGeom(height, portrait)
  const gap = Math.round(vidW * 0.03)
  const maxChipW = Math.max(80, vidW - logoW - margin * 2 - gap)

  // Start smaller in portrait (relative to the narrow frame), then shrink until
  // the chip fits the room beside the logo so a long Hungarian date never overlaps it.
  let fs = portrait ? Math.max(12, Math.round(h * 0.033)) : Math.max(13, Math.round(h * 0.05))
  let padX = Math.round(fs * 0.55)
  ctx.font = fontOf(fs)
  let textW = ctx.measureText(text).width
  while (textW + padX * 2 > maxChipW && fs > 10) {
    fs -= 1
    padX = Math.round(fs * 0.55)
    ctx.font = fontOf(fs)
    textW = ctx.measureText(text).width
  }
  const padY = Math.round(fs * 0.4)
  const W = Math.ceil(textW) + padX * 2
  const H = Math.round(fs * 1.3) + padY * 2
  canvas.width = W
  canvas.height = H

  ctx = canvas.getContext('2d')
  const r = Math.round(fs * 0.28)
  ctx.fillStyle = 'rgba(20, 10, 8, 0.55)'   // matches the app's modal backdrop tint
  if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(0, 0, W, H, r); ctx.fill() }
  else ctx.fillRect(0, 0, W, H)
  ctx.font = fontOf(fs)
  ctx.fillStyle = '#fff'
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'
  ctx.fillText(text, padX, Math.round(H / 2))

  const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'))
  return new Uint8Array(await blob.arrayBuffer())
}

function argsFor(mode, { watermark, portrait, height, date }) {
  const OUT = ['-movflags', '+faststart', 'output.mp4']

  // Neither watermark nor portrait crop: keep the fast paths — "none"/"soft"
  // stream-COPY the video, "burn" needs only a simple -vf filtergraph.
  if (!watermark && !portrait) {
    const IN = ['-i', 'input.ts']
    if (mode === 'soft') {
      return [...IN, '-i', 'subs.srt',
        '-map', '0:v:0', '-map', '0:a:0', '-map', '1:0',
        '-c:v', 'copy', ...AAC, '-c:s', 'mov_text',
        '-metadata:s:s:0', 'language=hun', ...OUT]
    }
    if (mode === 'burn') {
      return [...IN, '-map', '0:v:0', '-map', '0:a:0',
        '-vf', `subtitles=subs.srt:fontsdir=fonts:force_style='${BURN_STYLE}'`,
        ...X264, ...AAC, ...OUT]
    }
    // none: video remux + AAC audio. Drop the TS timed-id3 data stream (0:v/0:a only).
    return [...IN, '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', ...AAC, ...OUT]
  }

  // Watermark and/or portrait: the picture is filtered, so the video is
  // re-encoded (audio transcoded to AAC, as everywhere). Inputs: 0=input.ts, then the logo (only
  // with a watermark) and subs.srt (only when the soft text track is muxed).
  const inputs = ['-i', 'input.ts']
  let logoInput = -1
  if (watermark) { logoInput = inputs.length / 2; inputs.push('-i', 'logo.png') }
  // The date bug rides along with the watermark (top-left), so it's only an input
  // when the watermark is on and a date was supplied.
  const withDate = watermark && !!date
  let dateInput = -1
  if (withDate) { dateInput = inputs.length / 2; inputs.push('-i', 'datebug.png') }
  const subInput = mode === 'soft' ? inputs.length / 2 : -1
  if (subInput >= 0) inputs.push('-i', 'subs.srt')

  // Build the main video chain, ending at a named pad; a watermark (if any) is
  // overlaid onto that pad. Crop to portrait FIRST so burned-in subtitles wrap to
  // the narrow frame instead of being rendered wide and then chopped by the crop.
  const chain = []
  if (portrait) chain.push(PORTRAIT_CROP)
  if (mode === 'burn') chain.push(`subtitles=subs.srt:fontsdir=fonts:force_style='${BURN_STYLE}'`)

  let fc = ''
  if (watermark) {
    // Base video (after any crop / burned-in subtitles), then overlay the logo
    // top-right and — when a date is supplied — the date bug top-left.
    let cur = '[0:v]'
    if (chain.length) { fc += `[0:v]${chain.join(',')}[vbase];`; cur = '[vbase]' }
    const { logoW, margin } = watermarkGeom(height, portrait)
    // Slight transparency (aa) so it reads as a watermark, not a UI element.
    fc += `[${logoInput}:v]scale=${logoW}:-1,format=rgba,colorchannelmixer=aa=0.92[wm];`
    if (withDate) {
      fc += `${cur}[wm]overlay=W-w-${margin}:${margin}[vwm];`
      fc += `[vwm][${dateInput}:v]overlay=${margin}:${margin}[vout]`
    } else {
      fc += `${cur}[wm]overlay=W-w-${margin}:${margin}[vout]`
    }
  } else {
    // Portrait crop (± burn) with no watermark: a plain linear chain.
    fc = `[0:v]${chain.join(',')}[vout]`
  }

  const subMap = subInput >= 0
    ? ['-map', `${subInput}:0`, '-c:s', 'mov_text', '-metadata:s:s:0', 'language=hun']
    : []
  return [...inputs, '-filter_complex', fc, '-map', '[vout]', '-map', '0:a:0',
    ...subMap, ...X264, ...AAC, ...OUT]
}

/**
 * Mux the fetched TS bytes into an MP4 with the chosen subtitle mode.
 *
 * @param {object} o
 * @param {Uint8Array} o.tsData    concatenated MPEG-TS bytes (from hlsFetch)
 * @param {string}  o.srt          SRT subtitle text (ignored when mode === 'none')
 * @param {'none'|'soft'|'burn'} o.mode
 * @param {boolean} o.watermark    overlay the Parlamonitor logo top-right (re-encodes)
 * @param {boolean} o.portrait     center-crop to a 9:16 portrait frame (re-encodes)
 * @param {number}  o.height       the video's height (to size the watermark)
 * @param {string}  o.date         sitting-day date shown top-left with the watermark ('' = none)
 * @param {(p:number)=>void} o.onProgress   encode progress 0..1 (meaningful when re-encoding)
 * @param {(m:string)=>void} o.onLog
 * @returns {Promise<Blob>} the MP4
 */
export async function muxClip({ tsData, srt, mode, watermark = false, portrait = false, height = 0, date = '', onProgress, onLog }) {
  const ff = await ensureLoaded({ onLog })

  let progressHandler = null
  if (onProgress) {
    progressHandler = ({ progress }) => onProgress(Math.max(0, Math.min(1, progress)))
    ff.on('progress', progressHandler)
  }
  try {
    await ff.writeFile('input.ts', tsData)
    if (mode !== 'none') await ff.writeFile('subs.srt', srt || '')
    if (mode === 'burn') {
      try { await ff.createDir('fonts') } catch { /* may already exist */ }
      await ff.writeFile('fonts/subfont.ttf', await fetchFile(fontURL))
    }
    if (watermark) await ff.writeFile('logo.png', await fetchFile(watermarkURL))
    const withDate = watermark && !!date
    if (withDate) await ff.writeFile('datebug.png', await makeDatePng(date, height, portrait))

    const code = await ff.exec(argsFor(mode, { watermark, portrait, height, date }))
    if (code !== 0) throw new Error(`ffmpeg exited ${code}`)

    const out = await ff.readFile('output.mp4')
    // Clean the virtual FS so repeated exports don't accumulate.
    for (const f of ['input.ts', 'subs.srt', 'logo.png', 'datebug.png', 'output.mp4']) {
      try { await ff.deleteFile(f) } catch { /* ignore */ }
    }
    return new Blob([out.buffer], { type: 'video/mp4' })
  } finally {
    if (progressHandler) ff.off('progress', progressHandler)
  }
}
