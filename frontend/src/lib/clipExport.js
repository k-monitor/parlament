// Client-side clip exporter engine (VIE-10) — ffmpeg.wasm.
//
// Everything runs in the browser: the (already public) MPEG-TS bytes are muxed
// into an MP4 with no server-side transcoding and no stored artefact, so the
// deployment pays nothing but the bytes the user already streamed.
//
// The single-thread @ffmpeg/core is used deliberately: the "none"/"soft" paths
// only stream-COPY the H.264 video + MP3 audio into MP4 (fast, seconds), and a
// single-thread core needs no SharedArrayBuffer — so the app avoids the
// COOP/COEP cross-origin-isolation headers that would otherwise break the
// cross-origin parliament video and MP photos. Only "burned-in" re-encodes with
// libx264 (slower) — the UI warns before that path.
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
        if (onProgress && e && e.total) onProgress(e.received / e.total)
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

// Logo placement: scaled to ~24% of the video width, pinned top-right with a
// small margin. The renditions are 16:9, so width ≈ height·16/9.
function watermarkGeom(height) {
  const vidW = height ? Math.round((height * 16) / 9) : 854
  return { logoW: Math.round(vidW * 0.24), margin: Math.max(8, Math.round(vidW * 0.02)) }
}

function argsFor(mode, { watermark, height }) {
  const OUT = ['-movflags', '+faststart', 'output.mp4']

  // No watermark: keep the fast paths — "none"/"soft" stream-COPY the video.
  if (!watermark) {
    const IN = ['-i', 'input.ts']
    if (mode === 'soft') {
      return [...IN, '-i', 'subs.srt',
        '-map', '0:v:0', '-map', '0:a:0', '-map', '1:0',
        '-c:v', 'copy', '-c:a', 'copy', '-c:s', 'mov_text',
        '-metadata:s:s:0', 'language=hun', ...OUT]
    }
    if (mode === 'burn') {
      return [...IN, '-map', '0:v:0', '-map', '0:a:0',
        '-vf', `subtitles=subs.srt:fontsdir=fonts:force_style='${BURN_STYLE}'`,
        ...X264, '-c:a', 'copy', ...OUT]
    }
    // none: pure remux. Drop the TS timed-id3 data stream (0:v/0:a only).
    return [...IN, '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', ...OUT]
  }

  // Watermark ON: overlay the logo top-right. That needs the picture composited,
  // so the video is re-encoded (audio still copied). Inputs: 0=input.ts,
  // 1=logo.png, and 2=subs.srt only when the soft text track is also muxed.
  const { logoW, margin } = watermarkGeom(height)
  const inputs = ['-i', 'input.ts', '-i', 'logo.png']
  const subInput = mode === 'soft' ? 2 : -1
  if (subInput >= 0) inputs.push('-i', 'subs.srt')

  let fc = ''
  let vsrc = '[0:v]'
  if (mode === 'burn') {
    fc += `[0:v]subtitles=subs.srt:fontsdir=fonts:force_style='${BURN_STYLE}'[vs];`
    vsrc = '[vs]'
  }
  // Slight transparency (aa) so it reads as a watermark, not a UI element.
  fc += `[1:v]scale=${logoW}:-1,format=rgba,colorchannelmixer=aa=0.92[wm];`
  fc += `${vsrc}[wm]overlay=W-w-${margin}:${margin}[vout]`

  const subMap = subInput >= 0
    ? ['-map', `${subInput}:0`, '-c:s', 'mov_text', '-metadata:s:s:0', 'language=hun']
    : []
  return [...inputs, '-filter_complex', fc, '-map', '[vout]', '-map', '0:a:0',
    ...subMap, ...X264, '-c:a', 'copy', ...OUT]
}

/**
 * Mux the fetched TS bytes into an MP4 with the chosen subtitle mode.
 *
 * @param {object} o
 * @param {Uint8Array} o.tsData    concatenated MPEG-TS bytes (from hlsFetch)
 * @param {string}  o.srt          SRT subtitle text (ignored when mode === 'none')
 * @param {'none'|'soft'|'burn'} o.mode
 * @param {boolean} o.watermark    overlay the Parlamonitor logo top-right (re-encodes)
 * @param {number}  o.height       the video's height (to size the watermark)
 * @param {(p:number)=>void} o.onProgress   encode progress 0..1 (meaningful when re-encoding)
 * @param {(m:string)=>void} o.onLog
 * @returns {Promise<Blob>} the MP4
 */
export async function muxClip({ tsData, srt, mode, watermark = false, height = 0, onProgress, onLog }) {
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

    const code = await ff.exec(argsFor(mode, { watermark, height }))
    if (code !== 0) throw new Error(`ffmpeg exited ${code}`)

    const out = await ff.readFile('output.mp4')
    // Clean the virtual FS so repeated exports don't accumulate.
    for (const f of ['input.ts', 'subs.srt', 'logo.png', 'output.mp4']) {
      try { await ff.deleteFile(f) } catch { /* ignore */ }
    }
    return new Blob([out.buffer], { type: 'video/mp4' })
  } finally {
    if (progressHandler) ff.off('progress', progressHandler)
  }
}
