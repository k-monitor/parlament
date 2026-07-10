// Classical multidimensional scaling (principal-coordinates analysis), pure JS
// and dependency-free. Given an n×n symmetric distance matrix it returns n 2-D
// points whose pairwise Euclidean distances approximate the input — used to lay
// out the faction map, where distance = "how rarely two factions vote together"
// (1 − agreement), so the blocs fall out spatially.
//
// Method: double-centre the squared-distance matrix into the Gram matrix
// B = −½·J·D²·J (J = I − 1/n), then take its top-2 eigenvectors scaled by the
// square root of their eigenvalues. B is small (≤ ~13×13 here) and symmetric, so
// a cyclic Jacobi eigensolver is more than fast enough.

// Cyclic Jacobi eigenvalue decomposition of a symmetric matrix. Returns the
// eigenvalues and `vectors[i][k]` = the i-th component of the k-th eigenvector
// (eigenvectors as columns).
function jacobiEigen(input, maxSweeps = 100) {
  const n = input.length
  const A = input.map((r) => r.slice())
  const V = Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)))

  for (let sweep = 0; sweep < maxSweeps; sweep++) {
    let off = 0
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) off += A[p][q] * A[p][q]
    if (off < 1e-14) break

    for (let p = 0; p < n; p++) {
      for (let q = p + 1; q < n; q++) {
        const apq = A[p][q]
        if (Math.abs(apq) < 1e-18) continue
        // Rotation that annihilates A[p][q] (Numerical-Recipes tangent form).
        const theta = (A[q][q] - A[p][p]) / (2 * apq)
        const t = theta === 0
          ? 1
          : Math.sign(theta) / (Math.abs(theta) + Math.sqrt(theta * theta + 1))
        const c = 1 / Math.sqrt(t * t + 1)
        const s = t * c
        // A ← Jᵀ·A·J, applied as column then row updates.
        for (let k = 0; k < n; k++) {
          const akp = A[k][p], akq = A[k][q]
          A[k][p] = c * akp - s * akq
          A[k][q] = s * akp + c * akq
        }
        for (let k = 0; k < n; k++) {
          const apk = A[p][k], aqk = A[q][k]
          A[p][k] = c * apk - s * aqk
          A[q][k] = s * apk + c * aqk
        }
        for (let k = 0; k < n; k++) {
          const vkp = V[k][p], vkq = V[k][q]
          V[k][p] = c * vkp - s * vkq
          V[k][q] = s * vkp + c * vkq
        }
      }
    }
  }
  return { values: A.map((r, i) => r[i]), vectors: V }
}

// Lay out an n×n distance matrix as n [x, y] points. A null distance (two
// factions that never cast on the same vote) is treated as maximally far (1).
export function classicalMDS(dist) {
  const n = dist.length
  if (n === 0) return []
  if (n === 1) return [[0, 0]]

  const D2 = dist.map((row) => row.map((d) => {
    const v = d == null ? 1 : d
    return v * v
  }))
  const rowMean = D2.map((r) => r.reduce((a, b) => a + b, 0) / n)
  const colMean = Array.from({ length: n }, (_, j) => {
    let s = 0
    for (let i = 0; i < n; i++) s += D2[i][j]
    return s / n
  })
  const grand = rowMean.reduce((a, b) => a + b, 0) / n
  const B = D2.map((r, i) => r.map((v, j) => -0.5 * (v - rowMean[i] - colMean[j] + grand)))

  const { values, vectors } = jacobiEigen(B)
  const order = values.map((_, i) => i).sort((a, b) => values[b] - values[a])
  const [a, b] = [order[0], order[1]]
  const la = Math.max(values[a] ?? 0, 0)
  const lb = Math.max(values[b] ?? 0, 0)
  const sa = Math.sqrt(la), sb = Math.sqrt(lb)
  return vectors.map((row) => [row[a] * sa, row[b] * sb])
}
