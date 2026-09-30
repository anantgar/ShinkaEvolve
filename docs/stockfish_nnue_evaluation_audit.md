# NNUE evaluation audit and production qualification

September 30, 2026. This audit follows the user's requirement that the Shinka
fork's `main` support readable, immutable engine context and withhold all tests
from mutation agents. The current campaign schema is `stockfish-inference-v2`.
Rebuild the image and prepare a new campaign; do not reuse v1 calibration.

## What was found and fixed

| Finding | Fix and verification |
|---|---|
| The seed exposed smoke fixtures and upstream tests; mutation agents received all build dependencies. | Curate the seed to engine source, required build scripts and attribution. Withhold upstream benchmark/perft code in a build-only bundle. Set `job.mutation_dependency_scope: runtime`, exposing only the frozen network. Canary tests inspect the source snapshot, synthetic Git history and dependency archive. |
| Production PGNs did not automatically include the special-move fixtures. | Add a private edge-case suite to every correctness pass, separate from the timed PGN distribution. |
| Eager evaluation did not sufficiently exercise chains of unevaluated accumulator frames. | Check both eager and lazy updates, plus fresh/cache-cleared outputs, both perspectives, null moves, siblings and undo. Repeat correctness after timing to expose persistent-state damage. |
| The minimum duration was checked on a probe, not every scored request. | Record the minimum over all actual baseline/candidate samples and reject the complete measurement if any are too short. |
| Aggregate uncertainty could conceal noisy workloads that moved in opposite directions. | Retain whole-round covariance in the score and separately gate each workload's uncertainty. |
| Per-call clock reads perturbed the kernel. | Remove them. Score only trusted host elapsed time around batches; no candidate-reported timing affects fitness. |
| Abort cleanup could miss the reference's separate attempt ID. | Give both containers the same durable attempt identity and different container names. Cleanup regression tests cover both. |
| UBSan could report a problem and continue. | Sanitizer builds use `-fno-sanitize-recover=all`; a diagnostic cannot silently earn a valid result. |
| The evolution runner converted failed/missing secure measurements into zero-score individuals, and could admit a failed seed as correct. | Persist measurement failures only in operator artifacts and the attempt log. They produce no Program, model reward, prompt fitness or selection entry. Require a successfully measured, correct seed. Regression tests cover malformed results, database failure and duplicate delivery. |
| A restart could lose the costs of failed measurements or recreate the seed if it was the only scored individual. | Restore failed-job costs once per job identity, and resume whenever any measured population exists. |

Independent score tests use a known Student-t critical value and manually
calculated, correlated workload observations. They also reject unequal work,
changing call counts, unbalanced ordering, missing/extra workloads, nonfinite
times and boolean call counts. Scaling all times to another unit leaves fitness
unchanged. These checks supplement real A/A, slow and incorrect controls.

## Evidence from this audit

- The final repository suite passed **850 tests**, with three skipped cases
  and two secret/live cases deselected. Ruff, the CI Mypy command,
  CloudFormation lint and wheel construction passed.
- A real Linux ARM ASan/UBSan build with recovery disabled matched **36,312 exact
  values** against the optimized reference across four reordered eager/lazy
  passes. This is smoke-corpus evidence, not exhaustive validation of future
  individuals.
- The v2 A/A control passed exact checks and the smoke gates at 1.0085 geometric
  speedup. The incorrect control received `correct=false`, score zero. The slow
  control's exact checks passed but its timing was rejected: aggregate log-SE
  0.0262 exceeded the smoke limit 0.02. **The v2 campaign was not frozen.**
- Two real Docker worker integration attempts, with only S3 stubbed, likewise
  returned failed measurements after **36,216 exact values** matched per attempt.
  One exceeded the per-workload noise gate (0.0662 versus 0.05); the other exceeded
  the aggregate gate (0.0245 versus 0.02). Their success assertions therefore
  failed. No thresholds were relaxed, samples removed or fitness admitted.

These results support correctness gating and failure isolation. They do **not**
qualify timing precision on this shared macOS/Docker host or on AWS. The older v1
all-controls pass in the handoff remains historical; it does not qualify v2.
Local logs, controls, raw diagnostic samples and sanitizer results are retained
under `examples/stockfish_nnue/.work/audit-*`, which is excluded from Git.

## Visibility and immutability

The agent should see the optimization contract, the allowed NNUE files, relevant
surrounding engine code, interfaces, architecture and fixed weights. It receives
aggregate fitness and correctness feedback. That is enough to reason about
implementation changes without revealing test cases or expected values.

The evaluator, fixtures, holdout PGNs, trusted baseline, private build context,
raw outputs and operator diagnostics remain outside the mutation snapshot and
its Git history. Immutable engine files are checked against content hashes before
compilation; edits outside the allowlist are rejected by the repository policy
as well. This is enforced acceptance policy, not merely a prompt or chmod bit.

The program being evaluated necessarily receives input positions. Hiding tests
from the mutation agent does not make runtime inputs unknowable to its binary,
or make published upstream fixtures secret. Keep the actual campaign datasets
and an untouched final holdout outside Git and outside mutation containers.
Inspect finalist diffs for benchmark recognition, memoization and shared state.

## Measurement and score

The target is exact integer inference. Each correctness comparison requires
identical raw and final values; timed streams require identical call counts and
checksums. Wrong results or resource violations receive zero fitness. Build,
protocol, timing-quality and infrastructure failures are separate failed jobs
and must not be interpreted as evidence that a candidate is fast or slow.
They remain in the attempt log, consume already-spent proposal budget, and are
excluded from population counts and rewards. In the default proposal-ID budget
mode, the run can finish short of its requested measured population; it reports
the missing generations. Diagnose failures before resuming. Retrying a candidate
must follow a campaign-wide policy fixed in advance, with failed measurements
retained; repeatedly rerunning until a favorable score appears biases selection.

For each complete round, compute the fixed weighted sum of workload log-speed
ratios. Fitness is `exp(mean - t_0.95,n-1 * SE)`. Whole paired rounds are the
statistical units; millions of NNUE calls are not millions of independent timing
observations. Balanced randomized AB/BA ordering controls order effects. No
outlier deletion or winner-dependent early stopping is allowed.

This is an approximate one-sided confidence bound under reasonably stable,
independent round errors. It is not a global 95% guarantee after thousands of
adaptive comparisons. Search selects noise as well as improvements; confirm
finalists on independent jobs, worker instances and new positions.

The score includes make/undo, accumulator orchestration, allocation, checksums
and protocol overhead. Parsing, network loading, compilation and startup are
excluded. It measures replay throughput, not pure arithmetic throughput, full
engine nodes/second or Elo. Detailed profiling belongs in a separate diagnostic
run; do not put tracing or a profiler inside the scored measurement.

## How thorough to make a real campaign

1. **Qualify the worker pool first.** Repeated A/A controls across at least three
   fresh worker instances, all three positive/negative controls, pinned images,
   compiler, AMI, CPU family and corpus. Resolve bias and noise before selection.
   The production A/A log-bias budget is 0.003, about 0.3%.
2. **Every individual.** Exact checks over the entire frozen fitness corpus,
   always including edge cases and eager/lazy/undo/null/fresh paths; resource
   limits; then paired timing. Start with 1,000–10,000 representative game traces
   if affordable, stratified by phase and material, and inspect measured coverage.
   Trace count is a starting budget, not proof of complete position coverage.
3. **Precision.** Production defaults are 24 rounds, every sample at least one
   second, aggregate log-SE at most 0.002 and per-workload log-SE at most 0.005.
   Baseline calibration targets twice the duration floor. These settings have
   not yet been qualified on AWS. If an unusually fast candidate falls below
   the floor, recalibrate a new campaign for everyone; do not silently alter its
   work or accept an under-measured speedup.
4. **Finalists.** Larger untouched holdout; ASan/UBSan and long stateful runs;
   multiple independent builds/jobs/worker instances; other supported ISAs;
   full-engine fixed-work correctness and speed with unchanged search. Test
   concurrent engine use before accepting shared mutable state. Aim for an
   uncertainty interval materially narrower than the claimed gain; for a
   sub-percent claim, roughly 0.1–0.2% precision is a useful qualification target,
   not an assurance from a fixed number of rounds.

The 60/25/15 workload weights are provisional. PGN-plus-sibling replay is an
approximation to search. Before a large run, profile the baseline search and
check the mix of refreshes, lazy updates, cache hits and propagation. A future
search-trace recorder would make this workload more representative. Keep that
profiling and the final holdout separate from evolutionary feedback.

No finite test suite proves that no bug can slip through. The purpose of these
layers is to detect distinct failure modes and make remaining uncertainty explicit.

## AWS instance plan

Use one on-demand `c8g.2xlarge` for the ARM canary, then a homogeneous pool when
calibration passes. It provides eight Graviton4 cores/vCPUs and 16 GiB RAM; that
leaves room for compilation, two runtime containers and the host. This is a
capacity recommendation, not a measured price/performance result for this task.
[AWS C8g specifications](https://aws.amazon.com/ec2/instance-types/c8g/)

For x86, use a separate `c8i.2xlarge` pool, or `c7i.2xlarge` when that is the target
deployment CPU. The template disables SMT on those Intel instances: four physical
cores, one thread per core and 16 GiB RAM. Select AVX2 as its own campaign; an ARM
improvement does not establish an x86 gain.
[AWS C8i specifications](https://aws.amazon.com/ec2/instance-types/c8i/),
[supported CPU options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cpu-options-supported-instances-values.html)

No GPU is needed for this inference-code campaign. Use one candidate at a time
per instance, serial baseline/candidate measurements on the same pinned core,
and no overlapping compilation or mutation on that host. The stack defaults to
zero workers. Avoid burstable/flex pools for measurement. Begin with one worker,
then 8–32 independent workers if the queue and budget justify it; confirm region
availability and quotas before deployment. Spot can serve exploratory work later,
with interrupted paired runs discarded and repeated in full.

## Inputs needed before deployment

- Target CPU family/ISA and the smallest improvement worth reliably detecting.
- AWS account/profile and region, suitable VPC/subnets, initial worker/cost cap.
  Supply names and identifiers, not credentials in chat.
- Representative production PGNs or permission to assemble them, and the intended
  deployment workload. Preserve a separate, untouched final dataset.

These decisions do not block code review or local harness verification. They do
block claiming production measurement quality or starting a meaningful large fleet.
