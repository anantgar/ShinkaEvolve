# Exact Stockfish NNUE inference optimization

This task evolves C++ NNUE implementation code and measures speed after exact
correctness checks. It does not train weights or change search. Every candidate uses Stockfish
`0a215d6c9e48856ef630013b8ab8312941a59057` and the SHA-256-pinned SFNNv16 network
in `task_manifest.json`.

The new fixed-search lane uses `stockfish-inference-v3` and
[`search_manifest.json`](search_manifest.json). See [SEARCH_PILOT.md](SEARCH_PILOT.md)
for its private fixtures, process-block scoring, qualification and bounded launch.
The older replay lane remains available as `stockfish-inference-v2` for diagnosis.
Always rebuild the image and prepare a new campaign when changing lanes.
See the [evaluation audit](../../docs/stockfish_nnue_evaluation_audit.md) for the
verification policy, known limits, instance choices and qualification requirements.

**Production timing is not qualified.** AWS controls found stable offsets
between identical replay programs that within-process error bars did not capture.
Large pages corrected a memory-context mismatch but did not remove all offsets.
Three-host, externally timed full-engine controls were more promising. The private
v3 lane is implemented and undergoing native qualification; it must pass its
controls before evolution. Do not start evolution on the old replay pool. See the
[completed timing investigation](../../docs/stockfish_timing_investigation_2026-09-30.md).

The first target is Linux ARM with NEON dot product, suitable for a homogeneous
Graviton pool. `--target avx2` prepares a separate x86 campaign. Build and evaluate
on the target architecture; do not use QEMU timings. The native Apple build path
in `build.py` is useful for harness development, but the production scheduler
uses Linux containers.

## What is implemented

- Pinned source/net preparation and a frozen baseline runtime artifact.
- File-hash enforcement before compilation; only the listed NNUE files may change.
- Readable surrounding engine source, with tests, fixtures, upstream benchmark
  implementations and original Git history omitted from the mutation snapshot.
  Only the network dependency is exposed to agents; keep
  `job.mutation_dependency_scope: runtime` in the generated configuration.
- Separate networkless build, candidate, and reference containers. No AWS or LLM
  credentials, Docker socket, baseline binary or private corpus file is mounted
  in the candidate. The evaluator sends the needed positions through the protocol.
- Persistent replay service with startup, network loading, FEN parsing and move
  parsing outside scored requests.
- Exact raw and final static evaluations, fresh/eager/lazy incremental agreement,
  make/undo/sibling paths, null moves, reordered traces, Chess960 and special moves.
- Warm incremental, fresh accumulator and hot propagation workloads; identical
  call counts and output checksums for each baseline/candidate pair.
- External wall-clock measurements, balanced AB/BA ordering, CPU affinity, memory
  and binary growth limits, private raw samples, and a noise rejection threshold.
- A/A, deliberately slow, and incorrect controls; game-level PGN splitting;
  campaign freezing; local and asynchronous AWS submission CLIs.
- S3 content-addressed artifacts, SQS FIFO jobs, dedicated EC2 workers, visibility
  heartbeats, cancellation, timeouts, retries after host loss and a dead-letter queue.

## Legacy replay score and shared limits

Correctness/resource violations receive `correct=false, combined_score=0`.
Infrastructure/protocol/noise failures receive no fitness. Correct but slower
implementations remain valid candidates with a score below 1.
Failed measurements are recorded outside the population and model rewards;
their proposal costs remain accounted for on restart. A missing, failed or
incorrect seed stops initialization. In the default proposal-ID budget mode,
measurement failures can leave a run short of its requested measured population.
Inspect the attempt log and private diagnostics before resuming; use a fixed
retry policy rather than rerunning candidates until a favorable score appears.

For each complete paired round:

```text
L_i = 0.60 log(T_baseline_incremental / T_candidate_incremental)
    + 0.25 log(T_baseline_refresh     / T_candidate_refresh)
    + 0.15 log(T_baseline_hot         / T_candidate_hot)
score = exp(mean(L) - t_0.95,n-1 * stdev(L) / sqrt(n))
```

Use total duration over identical work, not a mean of per-position speed ratios.
The statistical units are paired rounds, not individual NNUE calls. The default
24 rounds and one-second minimum samples are starting settings, not a promise of
resolution at a particular percentage. Every timed request must meet the duration
floor. Aggregate log-SE must be at most 0.002; individual workload log-SE at most
0.005. Calibrate on the actual worker hardware. Smoke settings are deliberately
looser and cannot qualify a production pool.
The 60/25/15 weights are provisional; measure baseline search workload frequencies
and settle them before freezing a real campaign.

The operator-only `diagnose.py` measures variation across fresh process pairs:

```bash
python -m examples.stockfish_nnue.diagnose \
  --campaign /private/prepared-campaign \
  --output /private/new-diagnostics --blocks 8 --pairs 6
```

Run it on a dedicated native Linux worker with Docker and access to kernel
process/cgroup counters. Both roles use the same trusted artifact. Its fixed
4096/2048/1024 workload pass counts and one-second probe floor are diagnostic
settings, not portable calibration defaults. It never freezes a campaign or
submits fitness. Keep outputs outside mutation snapshots; archive completed and
failed attempts. The allocation options require the explicit diagnostic runtime
artifacts described in the investigation report.

**The authoritative time includes immutable make/undo, accumulator orchestration,
checksum, allocation and a small protocol overhead.** It excludes parsing and
startup. Per-call timers have been removed; the host measures complete requests.
This conservatively dilutes kernel gains. It is not full-engine nodes/second or
an Elo estimate. Run detailed profiling separately from scored measurements.

Finite test coverage cannot prove equivalence on every legal position. Review
finalists for undefined behavior, benchmark detection and result memoization;
run fresh holdouts, sanitizers, other required ISAs, and full-engine correctness
and speed checks. Reject new shared mutable state unless separately tested for
thread safety. Do not select a winner from a single favorable confidence bound:
evolution creates many comparisons, and finalists need independent repeats.

## Build the image and run a public smoke campaign

This application is maintained on `codex/stockfish-inference-aws`; only reusable
Shinka framework support belongs on `main`.

From the Shinka repository root, with Docker running and the project installed:

```bash
uv pip install --python .venv/bin/python -e '.[stockfish]'
docker build -t shinka-stockfish-inference:local examples/stockfish_nnue
docker image inspect shinka-stockfish-inference:local --format '{{json .RepoDigests}}'
```

Use the printed `name@sha256:...` as `NNUE_IMAGE` below. Older Docker image stores
may require pushing and pulling the image to obtain its registry digest. The
base image is pinned; publish the complete resulting compiler image once and
reuse that digest on every worker. Package repositories are consulted only at
image build time, never inside a candidate evaluation.

```bash
NNUE_IMAGE='your-image@sha256:your-64-hex-digest'
.venv/bin/python -m examples.stockfish_nnue.prepare \
  --output examples/stockfish_nnue/.work/smoke \
  --image "$NNUE_IMAGE" --smoke
.venv/bin/python -m examples.stockfish_nnue.calibrate \
  --campaign examples/stockfish_nnue/.work/smoke \
  --output examples/stockfish_nnue/.work/smoke-controls --freeze
```

Directories must be new. Preparation downloads the pinned upstream source and
99 MB network once, checks its full digest, and compiles the baseline in a
networkless container. The smoke corpus is public and small; never report its
results as a held-out optimization win. Docker Desktop results are development
checks, not Graviton performance claims.

`--dedicated-container-vm` is required when preparing on an intentionally
dedicated Linux host using rootful Docker. The EC2 worker explicitly declares
that boundary. Ordinary Linux developer machines should use rootless Docker.
On a dedicated rootful-Docker VM, run the trusted host coordinator as root, as
the worker service does, so it can clean up container-owned scratch files. The
candidate and build containers still use the unprivileged sandbox UID.

Evaluate a candidate or the unchanged seed without invoking an LLM:

```bash
.venv/bin/python -m examples.stockfish_nnue.run \
  --campaign examples/stockfish_nnue/.work/smoke \
  --state examples/stockfish_nnue/.work/manual-check
# Add --candidate /absolute/path/to/your/Stockfish/candidate.
```

State and results must be outside the candidate and evaluator directories.
Only allowlisted public metrics enter Shinka feedback. The operator's private
CAS contains complete measurement samples and diagnostics.

## Prepare a real campaign

For AWS hardware qualification before choosing production data, prepare with
`--smoke --strict-timing`. This keeps the 24-round, one-second duration and strict
noise/bias gates while using the public smoke corpus. It remains a smoke corpus
and does not qualify coverage of a production workload. The public agent contract
states the target architecture and compiler without exposing test inputs.

Obtain representative PGNs whose provenance and redistribution permissions you
have recorded, covering varied openings, middlegames and endings. Split by
deduplicated complete game before making trace chunks:

```bash
.venv/bin/python -m examples.stockfish_nnue.corpus \
  --pgn games.pgn --output examples/stockfish_nnue/.work/corpus-v1 --seed 73013
.venv/bin/python -m examples.stockfish_nnue.prepare \
  --output examples/stockfish_nnue/campaign \
  --image "$NNUE_IMAGE" --mutation-image "$MUTATION_IMAGE" \
  --corpus examples/stockfish_nnue/.work/corpus-v1/holdout.json
```

The importer rejects malformed PGNs, deduplicates games independent of their
headers, splits complete games, emits bounded 96-ply segments, and records source
hash and seed. Sibling moves are selected deterministically at load time and
rotated across correctness passes. This approximates search traversal; it does
not claim to reproduce a search-recorded distribution. Keep a further untouched
PGN set for finalist confirmation. Special-move fixtures are always added to the
private correctness suite and do not change the PGN timing distribution. Neither
these fixtures nor the importer's `public.json` development split is mounted in
the agent. That filename is an importer convention, not permission to expose it.

Configure the campaign's `shinka.yaml` for the intended worker pool, run all
three calibration controls **on that pool**, then use `calibrate --freeze`.
The A/A bias limit applies to every workload as well as the aggregate: stable
component errors can cancel in an apparently neutral weighted score. Frozen
campaigns record `calibration_policy: aggregate-and-workload-aa-bias-v1`.
Do not reuse an older aggregate-only calibration without fresh qualification.
This records pass counts per workload and calibration identity, updates the
manifest's dependency hash, and copies the calibration report into the campaign.
Use a new state/run directory after freezing. Do not modify manifests, weights,
corpora, images or hardware during evolution. A frozen corpus that runs below
the minimum sample duration fails closed and needs a new calibration/campaign.

For a separate sanitizer campaign, prepare with `--sanitize`. Its times are not
comparable with optimized campaign times. Sanitizer and fresh-holdout checks
remain mandatory finalist checks, not expensive work on every timing candidate.

## Run Shinka

Set the campaign's digest-pinned `evo.mutation_image` to your existing secure
headless image, supply `agent_auth_profiles`, credential environment names, and
the provider-only network/proxy according to `docs/system_architecture.md`.
The generated template intentionally contains no credentials. The evaluation
CLI needs no LLM authentication; evolution does.

```bash
shinka_run --task-dir examples/stockfish_nnue/campaign \
  --config-fname shinka.yaml --evaluation-mode secure \
  --results_dir results/stockfish-inference-v1 --num_generations 10
```

Start with a 5–10-candidate canary, inspect diffs and rejection reasons, then
increase the generation budget. Keep `max_evaluation_jobs: 1` for local timing.
For AWS, set `job.backend: aws` and the AWS fields below; local proposer CPU count
does not limit remote evaluation concurrency.

## AWS deployment and submission

`aws/stack.yaml` provisions an encrypted private artifact bucket, FIFO queue,
DLQ/alarm, limited worker IAM role and a homogeneous EC2 Auto Scaling group.
It defaults to **zero running workers**. It does not create a VPC or assume a
default network; provide subnets with outbound access. Pick one exact Amazon
Linux 2023 AMI and one instance type per campaign. Use matching ARM or x86
images. Do not mix CPU generations in a pool. The default is `c8g.2xlarge`;
`c8i.2xlarge` is available for a separate x86 campaign. Intel worker options use
one thread per core to avoid SMT contention. No GPU is required.
`PurchaseOption=spot` is the default; use `PurchaseOption=on-demand` if Spot
capacity is unavailable. Keep one instance type per campaign in either case.
The first direct EC2 Spot tests, including their exact scope and host setup
findings, are recorded in the
[September 30 canary report](../../docs/stockfish_aws_canary_2026-09-30.md).

1. Publish the complete inference image to ECR and record its digest. Prepare
   with that ECR digest and pull it on the submitting host too. Workers refresh
   ECR authentication before pulls; credentials never enter candidate containers.
2. Build this branch's Shinka wheel with `uv build --wheel`, calculate its
   SHA-256, and deploy the stack with `DesiredWorkers=0`:

```bash
aws cloudformation deploy --stack-name shinka-stockfish \
  --template-file examples/stockfish_nnue/aws/stack.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId="$VPC_ID" SubnetIds="$SUBNET_IDS" AmiId="$AMI_ID" \
    WorkerWheelSha256="$WHEEL_SHA256" DesiredWorkers=0
aws cloudformation describe-stacks --stack-name shinka-stockfish \
  --query 'Stacks[0].Outputs'
```

3. Upload the wheel to the output bucket at `WorkerWheelKey` (default
   `bootstrap/shinka_evolve-0.0.7-py3-none-any.whl`). The startup script verifies
   its digest before installing it. Use `WorkerWheelKey` when the version changes.
4. Set desired capacity to one using the output group name, calibrate and freeze,
   then increase capacity as needed:

```bash
aws s3 cp dist/shinka_evolve-0.0.7-py3-none-any.whl \
  "s3://$ARTIFACT_BUCKET/bootstrap/shinka_evolve-0.0.7-py3-none-any.whl"
aws autoscaling set-desired-capacity --auto-scaling-group-name "$WORKER_GROUP" \
  --desired-capacity 1
```

Set these fields in the generated campaign configuration:

```yaml
job:
  backend: aws
  aws_region: us-east-1
  aws_bucket: YOUR_STACK_BUCKET
  aws_queue_url: YOUR_STACK_QUEUE_URL
  aws_prefix: shinka
  aws_job_timeout_seconds: 7200
  queue_timeout_seconds: 7200
  cpu_set: '2'
max_evaluation_jobs: 32
```

Merge these fields into the existing job section; retain its image, build,
runtime and resource settings. Use the normal AWS credential chain on the
submitting host. The operator needs access to the stack's S3 objects, bucket
listing, and SQS send/attributes APIs. Candidate and mutation containers receive
no AWS identity.

```bash
.venv/bin/python -m examples.stockfish_nnue.calibrate \
  --campaign examples/stockfish_nnue/campaign \
  --output examples/stockfish_nnue/.work/aws-calibration --backend aws --freeze
.venv/bin/python -m examples.stockfish_nnue.run \
  --campaign examples/stockfish_nnue/campaign \
  --state examples/stockfish_nnue/.work/submission-1 --backend aws --submit-only
# The printed durable job id can be collected with the same --state and --job-id.
```

Each worker holds a host-wide lock through build and evaluation. Baseline and
candidate run serially on the same CPU; different EC2 instances evaluate different
candidates. Scale workers and Shinka's remote concurrency together. Start with
Spot capacity, with on-demand fallback as needed; interrupted jobs need a full rerun of their paired
measurements. The queue tolerates duplicate delivery and publishes the first
terminal result conditionally. It does not promise exactly-once computation.

Cancellation writes a durable marker checked roughly every 15 seconds, plus AWS
API latency, including during compilation. Execution and queue waits are bounded.
Visibility extensions stay below SQS's 12-hour ceiling; the maximum job duration is 39,000
seconds. Host/process crashes can retry; completed candidate build/protocol failures
are terminal. Exhausted transport/host retries enter the DLQ; the submitting
scheduler also has a finite deadline. The CloudWatch alarm has no notification
destination until an operator configures one.

Use SSM and `journalctl -u shinka-worker` to inspect boot/worker problems. Complete
private results and operator logs live under `shinka/jobs/<job-id>/result.json`,
with content-addressed artifacts under `shinka/objects/`. Public views contain
only the allowlisted metrics. Successful jobs release their local disk artifacts
after upload. The S3 bucket is retained on stack deletion to preserve evidence;
plan retention separately. Scale the group back to zero when finished:

```bash
aws autoscaling set-desired-capacity --auto-scaling-group-name "$WORKER_GROUP" \
  --desired-capacity 0
```

For final measurements, bake and reuse a worker AMI after bootstrap to freeze
host dependencies too. Keep the recorded worker requirements, AMI, CPU family,
kernel, image digest, corpus/net/source hashes and raw paired samples.

AWS behavior follows the official [SQS visibility documentation](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html)
and [EC2 metadata options](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-ec2-launchtemplate-metadataoptions.html).

## Verification and other research directions

```bash
.venv/bin/python -m pytest tests/test_stockfish_inference.py tests/test_secure_aws.py -q
SHINKA_STOCKFISH_CAMPAIGN=/absolute/path/to/prepared/smoke \
  .venv/bin/python -m pytest tests/test_stockfish_aws_integration.py -m integration -q
```

The integration test uses real builds and separate Stockfish containers, with
S3 transport stubbed. It requires a successfully measured result, so a correct
noise rejection fails that assertion. Retain those diagnostics and qualify the
worker; do not weaken the gates to make a development host pass. It is not a live
AWS provisioning test. See
[`../../docs/stockfish_shinka_research_options.md`](../../docs/stockfish_shinka_research_options.md)
for weight training, architecture/search co-design and other Shinka applications.
The replay executable links Stockfish and must be distributed under its GPLv3
terms with corresponding source. The pinned source snapshot retains Stockfish's
license and attribution files.

## Upstream patch status

See [submission status](SUBMISSION.md) for the standalone Stockfish draft PR,
completed ARM evidence and remaining validation. Intel candidate timing is
incomplete; the October 3 AWS batch is shut down and cleaned up.
