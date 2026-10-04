# Private fixed-search pilot

The September 30 pilot and independent synthetic holdout are complete; the
finalist measured 2.33% and 2.26% faster fixed searches, respectively, with exact
checks and sanitizers passing. AWS workers and monitoring were cleaned up.
See [validation and submission](VALIDATION.md) for the next steps. The protocol
below describes reusable setup; it is not an active run status.

The v3 lane keeps the existing NNUE allowlist. Search, network weights, compiler
flags, adapters and measurement code are frozen. The mutation agent receives
surrounding engine source and the read-only network dependency. It receives no
test code, fixtures, reference executable, evaluator, operator state or original
Git history. Both snapshot validation and the private builder reject changes
outside the allowlist, additions, deletions and symlinks.

The objective is faster execution with identical behavior. No engine matches or
game outcomes are used. A fixed-depth search exercises Stockfish's actual NNUE
allocation, caches, updates and propagation in their engine context. Replay
microbenchmarks remain diagnostic because their persistent-process timing was
not reliable enough for fitness.

## Verification and score

The private builder links two executables from the same NNUE object files: the
ordinary engine and the trusted exact-output checker. The latter verifies raw
and final values, fresh/eager/lazy updates, make/undo, sibling branches, null moves,
special moves and Chess960 over four shuffled rounds before timing.

For timing, every process block runs four fresh containers in ABBA or BAAB order,
with only one engine resident at a time and a separate executable copy each time.
Each engine uses one thread and a fixed hash size.
Every position starts with `ucinewgame` and a readiness barrier. A search ends
only at `bestmove`. The controller checks score, depth, selective depth, PV,
best/ponder moves, node count, call count and a host-recomputed checksum against
the frozen reference. Reference results must also be deterministic across blocks.
Reported UCI time and NPS are ignored.

The host times whole requests. Startup and initial network loading are outside
timing; reset, position setup, search, immutable protocol and checksum overhead
are inside. These fixed costs dilute an NNUE gain. The result is a fixed-search
wall-time speedup, not isolated kernel throughput or an Elo estimate.

The default is 24 independent process blocks. Every process performs a warmup
and a timed request; each block yields two balanced AB/BA pairs. The ABBA/BAAB
choices are balanced and shuffled in advance, and each block uses one case order.
This matches both roles' early/late positions and removes simultaneous residency
of two separately allocated networks. The earlier paired-process design remains
available only to reproduce the archived diagnostic campaign.
For block `b`, take the mean of its paired log duration ratios. Fitness is:

```text
exp(mean(block_log_ratios) - t(0.95, blocks - 1) * block_standard_error)
```

Repeated rounds do not count as additional independent observations. All samples
are retained, including failing samples. There is no outlier trimming or stopping
when a favorable score appears. Every sample must exceed one second and the
between-block log standard error must be at most 0.002. A reference-only probe
chooses fixed pass counts before controls, targeting twice the duration floor.

Wrong outputs or resource violations receive zero fitness and `correct=false`.
Protocol, timeout, reference nondeterminism and excessive timing noise receive
no fitness. Private checkpoints preserve partial evidence. Finalists still need
source review, sanitizers, fresh positions and independent confirmation; a
four-generation pilot cannot establish a general Stockfish improvement.

Checkpoints retain each distinct raw output once in a compressed, hash-checked
catalog, with compact references from the timing records. Completed blocks are
compacted once. Each snapshot is self-contained, including for failed AWS jobs;
`decode_search_checkpoint` restores the raw evidence. Bulk checkpoint writes
occur after timing, not between a process's warmup and timed request. Exception
handlers force a final snapshot, including a failed warmup. This corrects the
original recorder's repeated serialization of all earlier raw results. The
timing index still grows with the declared block count; storage regression tests
bound total writes at the 128-block pilot size. Full final diagnostics retain the
original raw format. This recording fix must be qualified in a new campaign.

## Prepare and qualify

Use dedicated native Linux hosts, a pinned compiler/runtime image and one
measurement at a time per host. On a rootful dedicated VM, run the controller as
the same unprivileged UID configured for containers; this permits cleanup and
writable session homes without weakening isolation. Give that host account the
required Docker access. The agent never receives the Docker socket.

The private synthetic corpus generator constructs legal continuations without
playing matches. Its output is explicitly pilot data, not a representative
production holdout. Generate it outside the mutation source:

```bash
python -m examples.stockfish_nnue.pilot_corpus \
  --seed 20930930 --output /private/pilot-corpus.json
python -m examples.stockfish_nnue.prepare \
  --manifest examples/stockfish_nnue/search_manifest.json \
  --output /private/campaign --cache /private/cache --target graviton \
  --image registry/compiler@sha256:REPLACE_WITH_DIGEST \
  --corpus /private/pilot-corpus.json --pilot --dedicated-container-vm
python -m examples.stockfish_nnue.search_campaign freeze-work \
  --campaign /private/campaign --output /private/work-probe
python -m examples.stockfish_nnue.search_campaign controls \
  --campaign /private/campaign --output /private/primary-controls
```

Copy the prepared campaign with its fixed work to a second identical worker and
run `controls --controls aa`. Keep its absolute dependency URLs valid. The
primary runs A/A, injected mild/gross slowdowns and a wrong-output control. The
mild control adds 128 volatile-loop iterations; this does not assert a known
percentage slowdown. Its upper one-sided 95% speedup bound must be below one.
The gross control adds 4,000 iterations and must measure at least 5% slower.
The wrong control must fail exact output verification, not merely crash.

Both A/A controls require their complete two-sided 95% interval to fit inside
the declared ±0.003 log-speed equivalence margin. Freeze only after all pass:

```bash
python -m examples.stockfish_nnue.search_campaign freeze \
  --campaign /private/campaign \
  --primary /private/primary-controls/controls.json \
  --replication /private/replication-controls.json
```

Qualification binds the exact manifest, private evaluator/data/baseline, job
settings and evidence hashes. Replication requires a different worker boot ID.
Do not repeat failed controls until they pass. Preserve failures, investigate,
and create a new declared campaign when changing the protocol or fixtures.

## Four-generation launch and monitoring

First run an actual edit-and-resume canary through the secure Headless provider,
using the pinned mutation image, minimal Codex auth and provider-only egress.
Its private JSON proof records the model/image and successful editing, session
resume and credential removal. The launcher verifies this proof and qualification.

```bash
python -m examples.stockfish_nnue.mini_run \
  --campaign /private/campaign --results /private/mini-results \
  --mutation-image registry/agent@sha256:REPLACE_WITH_DIGEST \
  --auth-profile /private/minimal-auth \
  --provider-canary /private/secure-canary/result.json
```

This uses `headless/codex@gpt-6-astra?effort=high`, a measured seed plus four
proposal generations, one island, one proposal/evaluation slot, no auxiliary
models, and a 20-minute proposal timeout. Proposal-ID budgeting bounds the run;
failed proposals may leave fewer than five measured candidates. The launcher
does not silently retry a completed/failed results directory or alter its config.
Mutation/build and measurement do not overlap on the host.

Monitor authoritative job states/checkpoints, `programs.sqlite`, attempt logs and
candidate artifacts. Archive each candidate and all controls before terminating
Spot hosts. Never count an interrupted or noisy evaluation as a bad individual
or select a favorable retry. Preserve only redacted provider diagnostics and
exclude auth profiles and durable provider homes from evidence archives.

The September 30 operator state and heartbeat ID are recorded in
`../../HANDOFF_2026-09-30.md`. Production use still needs a representative,
independently held-out position corpus and a declared finalist policy.
