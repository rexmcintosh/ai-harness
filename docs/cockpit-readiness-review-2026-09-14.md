# Initiative readiness review

Owner-authorized feature: stage readiness, operating strength, Ideal State
outcomes, and linked work, initially populated privately for Attain Prep.

Initial review used `venice-delegate council-review --diff --panel code-review`.
Diff SHA-256: `54003edc36ccd69f78deb23b2943a9cc9883b1db037180765e04fe3d9a81f261`.
The panel requested changes in failure handling. The DeepSeek seat returned `na`
without a substantive assessment; the initial review is not treated as a clean
security review or merge approval.

## Corrections and evidence

- Missing `initiatives.js`: reproduced interrupted rendering with the browser
  fixture. A plain fallback now preserves All work and later sections.
- Source inspection: reproduced unrelated bad requirements hiding good source
  documents. The route now validates only the requested source. Unknown IDs are
  404; configured-source I/O, encoding, and structure failures are 503 with a
  content-free error-type log.
- File reads: read a bounded number of bytes from one regular-file descriptor,
  decode UTF-8 explicitly, and hash those same bytes. Source paths traverse from
  an opened project root, with symlink following disabled at each component.
  A 0600 configuration mismatch is visible without hiding source work.
- Dependencies: the existing 200-requirement bound already limited recursion,
  and cycles were rejected. The check and derivation now use an iterative
  dependency order, eliminating the recursion concern entirely.

The initial review's claim of read-induced data loss is not supported: these
paths never write source files. Duplicate JSON keys were already rejected by the
decoder hook, and the existing read checked size before parsing. The revised
reader additionally enforces the size on actual bytes read, avoiding stat/read
races. Authors must use atomic replacement for a coherent document; adding a
reader-only advisory lock would not coordinate writers and is not introduced.

Tests distinguish missing evidence from an absent practice, keep completed work
from promoting an outcome, and preserve exact source identity. Browser coverage
includes the missing-asset failure, stage selection, capability targets, outcome
uncertainty, unmapped work, and mobile layout. Private prepared business data and
customer documents are outside this public repository and the review diff.

The second review (diff SHA-256
`84e392071b039c09bc29ffe2eb3c9f3a43bdf80803871fe3fc042e5d87efabcd`)
returned a substantive security assessment with no injection or authorization
bypass identified. It required two further fixes: a missing catalog initiative ID
could raise before profile validation, and the assessment-file failure detail did
not distinguish absence from invalid content or read failure. Both were reproduced
and fixed; malformed identity leaves source work visible, and file failures now
provide specific explanations.

The third review (diff SHA-256
`399fd01e888499e899e7ab33c76aa6e04d5d9b825cdf1faf2000470c271a7b53`)
identified two further edge cases. Invalid repository-link types now produce an
unavailable assessment without interrupting the source-work snapshot. The source
boundary also normalizes those links before existing enrichment uses them.
Percent-encoded initiative fragments now decode safely, with invalid encodings
retaining the current selection. Both failures were reproduced before correction.

The suggested replacement of DOM IDs is unnecessary: profile IDs use a bounded
safe-character validation rule, DOM nodes are created through textContent and
attribute APIs, and lookups use getElementById rather than constructed selectors.
The repeated advisory-lock and file-race claims remain unsupported by the
single-descriptor, bounded, no-symlink reader and atomic author replacement
contract. No approval is inferred from the third review's empty security seat;
the second review provided the substantive security assessment.

Final verification after those corrections: all 102 cockpit tests pass, and the
browser regression passes, including percent-encoded initiative links and the
missing-script fallback. The private Attain preview also passes authenticated
source checks, native keyboard selection, and all four views at a 390px width.
The concrete review findings are resolved; disputed optional hardening is
adjudicated above. This records the review and verification outcome, not an
unconditional panel approval. Production remains a separate owner-approved release.
