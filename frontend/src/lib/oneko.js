// A pixel cat that chases the pointer, summoned by searching for "cica" or
// "macska" (SEA-13) — the site's one easter egg, and the X11 `oneko` every
// Hungarian who ever met a 90s desktop remembers.
//
// The cat itself is **vendored**, not ours: `oneko.js` by adryd, in the fork by
// tylxr59 that adds the burst of hearts when you click it —
// https://github.com/tylxr59/oneko.js — together with its `oneko.gif` sprite
// sheet (32×32 frames on an 8×4 grid), which is the sprite from the original X11
// toy. Vendored rather than depended on: it is one file that has barely changed
// in years, it is loaded by exactly one view, and an npm dependency for a joke
// would be a supply-chain risk out of all proportion to it.
//
//   Copyright © 2022 adryd
//
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction, including without limitation the rights
//   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//   copies of the Software, and to permit persons to whom the Software is
//   furnished to do so, subject to the following conditions:
//
//   The above copyright notice and this permission notice shall be included in
//   all copies or substantial portions of the Software.
//
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//   SOFTWARE.
//
// `startOneko()` below is upstream's script with six deliberate changes — keep
// this list current if it is ever re-vendored:
//
//   1. Upstream is an IIFE that puts a cat on the page for good the moment the
//      script loads. Here it is a function returning its own teardown, because
//      this cat is summoned by a search and has to be able to leave again.
//   2. The sprite comes from a bundled asset import instead of the
//      `data-cat` / `document.currentScript` dance: inside a module there is no
//      script tag to read, and this way Vite fingerprints (and, at 3 kB, inlines)
//      the sheet like every other asset.
//   3. The `mousemove` listener and the injected `.heart` stylesheet are kept on
//      the teardown path — upstream never removes either, which is fine for a
//      cat that lives until the tab closes and a leak for one that doesn't.
//   4. `zIndex = Number.MAX_VALUE` stringifies to exponent notation, which is not
//      a valid `z-index` and is dropped — leaving the cat to be painted over by
//      any positioned thing on the page. Replaced with a real integer, above the
//      site's own ceiling (1000, the share/embed popovers).
//   5. The `prefers-reduced-motion` gate moved out to `onekoForQuery`, where a
//      coarse-pointer gate joins it: a phone has no cursor to chase, so the cat
//      would only ever sit in the corner.
//   6. The hearts are the brand red rather than upstream's lilac, they take
//      themselves off the page (`heart.remove()`, which cannot throw once the cat
//      has left), and they are `pointer-events: none` — they land over the
//      results for a second and must not swallow a click on one.
//
// Everything the egg needs beyond that is here too: it is decoration, so it is
// `aria-hidden` and never enters the accessibility tree (A11Y-1), and a reader
// who asked for less motion is simply never shown it.
import onekoSprite from '../assets/oneko.gif?url'

// The words that summon it, matched as *stems* so Hungarian's suffixes come
// along for free: "cicák", "macskát", "macskás kérdés" all count. Accents are
// folded first, mirroring how search itself matches (§4B), so "cicat" works too.
// Deliberately a prefix match at a word boundary and not a bare `includes`:
// "macska" inside "macskaköves" is still a cat, but a substring rule would also
// fire on unrelated words that merely contain the letters.
const CAT_STEM = /(^|[^\p{L}\p{N}])(cica|macska)/u

function fold(s) {
  return s.toLowerCase().normalize('NFD').replace(/\p{M}/gu, '')
}

/** Whether an executed query is asking for a cat. */
export function isCatQuery(q) {
  return !!q && CAT_STEM.test(fold(q))
}

// Teardown of the one live cat, if there is one — so a second summon while it is
// already out is a no-op rather than a second cat.
let teardown = null

/** Send the cat away (if it is out). Safe to call at any time. */
export function dismissOneko() {
  if (!teardown) return
  teardown()
  teardown = null
}

/**
 * Summon or dismiss the cat for an executed search. Called with every query the
 * search view runs, so the cat arrives on "cica" and leaves again on the next
 * search that isn't about cats.
 */
export function onekoForQuery(q) {
  if (!isCatQuery(q)) return dismissOneko()
  if (teardown) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  if (!window.matchMedia('(pointer: fine)').matches) return
  teardown = startOneko()
}

// ---------------------------------------------------------------------------
// Vendored: oneko.js — https://github.com/tylxr59/oneko.js (see header)
// ---------------------------------------------------------------------------

function startOneko() {
  const nekoEl = document.createElement('div')

  let nekoPosX = 32
  let nekoPosY = 32

  let mousePosX = 0
  let mousePosY = 0

  let frameCount = 0
  let idleTime = 0
  let idleAnimation = null
  let idleAnimationFrame = 0

  const nekoSpeed = 10
  const spriteSets = {
    idle: [[-3, -3]],
    alert: [[-7, -3]],
    scratchSelf: [
      [-5, 0],
      [-6, 0],
      [-7, 0],
    ],
    scratchWallN: [
      [0, 0],
      [0, -1],
    ],
    scratchWallS: [
      [-7, -1],
      [-6, -2],
    ],
    scratchWallE: [
      [-2, -2],
      [-2, -3],
    ],
    scratchWallW: [
      [-4, 0],
      [-4, -1],
    ],
    tired: [[-3, -2]],
    sleeping: [
      [-2, 0],
      [-2, -1],
    ],
    N: [
      [-1, -2],
      [-1, -3],
    ],
    NE: [
      [0, -2],
      [0, -3],
    ],
    E: [
      [-3, 0],
      [-3, -1],
    ],
    SE: [
      [-5, -1],
      [-5, -2],
    ],
    S: [
      [-6, -3],
      [-7, -2],
    ],
    SW: [
      [-5, -3],
      [-6, -1],
    ],
    W: [
      [-4, -2],
      [-4, -3],
    ],
    NW: [
      [-1, 0],
      [-1, -1],
    ],
  }

  function onMouseMove(event) {
    mousePosX = event.clientX
    mousePosY = event.clientY
  }

  function init() {
    nekoEl.id = 'oneko'
    nekoEl.ariaHidden = true
    nekoEl.style.width = '32px'
    nekoEl.style.height = '32px'
    nekoEl.style.position = 'fixed'
    nekoEl.style.pointerEvents = 'auto'
    nekoEl.style.imageRendering = 'pixelated'
    nekoEl.style.left = `${nekoPosX - 16}px`
    nekoEl.style.top = `${nekoPosY - 16}px`
    nekoEl.style.zIndex = '2147483647' // (4) above the site's own ceiling
    nekoEl.style.backgroundImage = `url(${onekoSprite})` // (2) bundled asset

    document.body.appendChild(nekoEl)
    document.addEventListener('mousemove', onMouseMove)
    window.requestAnimationFrame(onAnimationFrame)
  }

  let lastFrameTimestamp

  function onAnimationFrame(timestamp) {
    // Stops execution if the neko element is removed from DOM — which is also
    // how (1) teardown stops the loop.
    if (!nekoEl.isConnected) {
      return
    }
    if (!lastFrameTimestamp) {
      lastFrameTimestamp = timestamp
    }
    if (timestamp - lastFrameTimestamp > 100) {
      lastFrameTimestamp = timestamp
      frame()
    }
    window.requestAnimationFrame(onAnimationFrame)
  }

  function setSprite(name, frame) {
    const sprite = spriteSets[name][frame % spriteSets[name].length]
    nekoEl.style.backgroundPosition = `${sprite[0] * 32}px ${sprite[1] * 32}px`
  }

  function resetIdleAnimation() {
    idleAnimation = null
    idleAnimationFrame = 0
  }

  function idle() {
    idleTime += 1

    // every ~ 20 seconds
    if (
      idleTime > 10 &&
      Math.floor(Math.random() * 200) == 0 &&
      idleAnimation == null
    ) {
      let avalibleIdleAnimations = ['sleeping', 'scratchSelf']
      if (nekoPosX < 32) {
        avalibleIdleAnimations.push('scratchWallW')
      }
      if (nekoPosY < 32) {
        avalibleIdleAnimations.push('scratchWallN')
      }
      if (nekoPosX > window.innerWidth - 32) {
        avalibleIdleAnimations.push('scratchWallE')
      }
      if (nekoPosY > window.innerHeight - 32) {
        avalibleIdleAnimations.push('scratchWallS')
      }
      idleAnimation =
        avalibleIdleAnimations[
          Math.floor(Math.random() * avalibleIdleAnimations.length)
        ]
    }

    switch (idleAnimation) {
      case 'sleeping':
        if (idleAnimationFrame < 8) {
          setSprite('tired', 0)
          break
        }
        setSprite('sleeping', Math.floor(idleAnimationFrame / 4))
        if (idleAnimationFrame > 192) {
          resetIdleAnimation()
        }
        break
      case 'scratchWallN':
      case 'scratchWallS':
      case 'scratchWallE':
      case 'scratchWallW':
      case 'scratchSelf':
        setSprite(idleAnimation, idleAnimationFrame)
        if (idleAnimationFrame > 9) {
          resetIdleAnimation()
        }
        break
      default:
        setSprite('idle', 0)
        return
    }
    idleAnimationFrame += 1
  }

  function explodeHearts() {
    const parent = nekoEl.parentElement
    const rect = nekoEl.getBoundingClientRect()
    const scrollLeft = window.scrollX || document.documentElement.scrollLeft
    const scrollTop = window.scrollY || document.documentElement.scrollTop
    const centerX = rect.left + rect.width / 2 + scrollLeft
    const centerY = rect.top + rect.height / 2 + scrollTop

    for (let i = 0; i < 10; i++) {
      const heart = document.createElement('div')
      heart.className = 'heart'
      heart.textContent = '❤'
      const offsetX = (Math.random() - 0.5) * 50
      const offsetY = (Math.random() - 0.5) * 50
      heart.style.left = `${centerX + offsetX - 16}px`
      heart.style.top = `${centerY + offsetY - 16}px`
      heart.style.transform = `translate(-50%, -50%) rotate(${Math.random() * 360}deg)`
      parent.appendChild(heart)

      setTimeout(() => {
        heart.remove()
      }, 1000)
    }
  }

  const style = document.createElement('style')
  style.innerHTML = `
    @keyframes heartBurst {
      0% { transform: scale(0); opacity: 1; }
      100% { transform: scale(1); opacity: 0; }
    }
    .heart {
      position: absolute;
      font-size: 2em;
      animation: heartBurst 1s ease-out;
      animation-fill-mode: forwards;
      color: #B22817;
      pointer-events: none;
    }
  `

  document.head.appendChild(style)
  nekoEl.addEventListener('click', explodeHearts)

  function frame() {
    frameCount += 1
    const diffX = nekoPosX - mousePosX
    const diffY = nekoPosY - mousePosY
    const distance = Math.sqrt(diffX ** 2 + diffY ** 2)

    if (distance < nekoSpeed || distance < 48) {
      idle()
      return
    }

    idleAnimation = null
    idleAnimationFrame = 0

    if (idleTime > 1) {
      setSprite('alert', 0)
      // count down after being alerted before moving
      idleTime = Math.min(idleTime, 7)
      idleTime -= 1
      return
    }

    let direction
    direction = diffY / distance > 0.5 ? 'N' : ''
    direction += diffY / distance < -0.5 ? 'S' : ''
    direction += diffX / distance > 0.5 ? 'W' : ''
    direction += diffX / distance < -0.5 ? 'E' : ''
    setSprite(direction, frameCount)

    nekoPosX -= (diffX / distance) * nekoSpeed
    nekoPosY -= (diffY / distance) * nekoSpeed

    nekoPosX = Math.min(Math.max(16, nekoPosX), window.innerWidth - 16)
    nekoPosY = Math.min(Math.max(16, nekoPosY), window.innerHeight - 16)

    nekoEl.style.left = `${nekoPosX - 16}px`
    nekoEl.style.top = `${nekoPosY - 16}px`
  }

  init()

  // (3) Everything this cat added to the page, undone: removing the element also
  // ends the animation loop on its next frame (see `onAnimationFrame`).
  return () => {
    document.removeEventListener('mousemove', onMouseMove)
    nekoEl.remove()
    style.remove()
  }
}
