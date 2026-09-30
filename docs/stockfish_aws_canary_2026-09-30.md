# Native AWS inference qualification — September 30, 2026

The Stockfish application lives on `codex/stockfish-inference-aws`. Fork `main`
at `340bc74` retains only reusable secure execution, AWS transport, dependency
visibility and failed-measurement handling. This was a normal corrective commit;
published Git history was preserved. Main passed 826 tests; the application
branch passed **855 tests** after the further calibration guard described below
(three skipped, two deselected). Ruff and the repository Mypy checks also pass.

## Test scope

Three matching Spot `c8g.2xlarge` instances were started in `us-east-1f`.
On-demand fallback was authorized but unnecessary. These are native Graviton4
tests of the secure local evaluator running **on EC2**, with host-side timing
around isolated containers. SSH and upload latency do not enter fitness.

The current AWS identity permits EC2 operations but denied CloudFormation,
ECR and SSM discovery. No IAM permissions were changed. The S3/SQS deployment
template and remote scheduler are not qualified by these direct EC2 tests.

All workers use the same image, wheel, Python requirements and prepared baseline.
One worker runs A/A, deliberately slow and deliberately wrong controls in sequence.
The other two run independent A/A controls. Each instance measures one job at a
time; reference and candidate use the same pinned core (`2`). Compilation and
artifact preparation finish before timing on that host.

Preparation uses `--smoke --strict-timing`: public development positions with
the production timing gates. The predeclared policy is 24 balanced paired rounds,
every measured request at least one second, aggregate log-SE at most 0.002,
per-workload log-SE at most 0.005, and A/A absolute log-bias at most 0.003.
Calibration targets twice the duration floor. Rejected runs stay in the evidence;
thresholds are not relaxed and samples are not discarded.

These controls establish neither production position coverage nor a speedup.
No LLM evolution, weight training, game matches or Elo claim is part of this run.

## A/A findings and a stricter calibration guard

The three independent A/A controls used byte-identical runtime artifacts and
matched **36,120 exact values each**. All used 8,192 incremental, 4,096 refresh and
2,048 hot passes per request. The minimum observed duration across the three runs
was 2.588 seconds. Recomputing the weighted logs, standard errors and confidence
bounds directly from raw samples matched the evaluator.

| A/A worker | Geometric speed | Confidence-bound score | Aggregate log-SE | Largest workload deviation from 1 |
| --- | ---: | ---: | ---: | --- |
| Primary | 0.997619 | 0.996148 | 0.000861 | Refresh: −1.24% |
| Repeat 1 | 0.997367 | 0.997250 | 0.000069 | Incremental: −0.41% |
| Repeat 2 | 0.998568 | 0.998113 | 0.000266 | Hot: −0.26% |

All three passed the original aggregate A/A limit and both noise gates. **That
was insufficient:** stable workload biases can partially cancel in the weighted
score. Small within-process standard errors do not account for this variation.
The cause of the workload differences has not been established; do not label
these differences an optimization or assume that more rounds in the same
process will remove them.

The branch now applies the existing A/A bias limit to **every workload as well
as the aggregate**, both in calibration and before writing a frozen manifest.
It records `calibration_policy: aggregate-and-workload-aa-bias-v1`. A regression
test covers perfectly canceling biases with zero timing variance; another uses
the primary worker's measured ratios and verifies that rejection changes no
campaign files. Neutral controls still freeze with updated dependency hashes.

Applying the new check to these preserved measurements rejects the primary
and repeat 1; repeat 2 passes. **The pool is not qualified for evolution.**
No thresholds were relaxed and no measurements were discarded or replaced by
favorable retries. The running primary process started before this guard was
added; any freeze it produces under its old policy is historical evidence only.
Do not reuse it for evolution.

Before a large run, investigate runtime placement and process-lifetime effects,
then predeclare and validate a protocol with independent process/worker blocks
and startup-order controls. Use a new campaign and preserve this failed
qualification. The final production corpus and workload weights also remain
unqualified.

After repeat 1 finished timing, a separate native ASan/UBSan build, with recovery
disabled, matched **36,312 exact values** against the optimized baseline. It
produced no sanitizer diagnostics and no fitness score.

The deliberately wrong implementation was rejected with `correct=false` and
score zero on repeat 1. A separate real-container AWS worker executor test on
repeat 2 passed in 536 seconds: **36,120 exact values**, 0.999512 geometric speed,
and a 0.999032 confidence-bound score. Its raw-sample recomputation also matched.
S3 was stubbed in that test; it does not exercise live SQS delivery or the
CloudFormation deployment.

At 07:27 UTC, the primary's deliberately slow control was still running its
original full 24-round protocol. The primary then runs its wrong control. Their
results remain pending; do not report an all-controls success. The two repeat
workers have finished and were terminated after their evidence archives were
downloaded and hash-verified.

## Reproduction identities

| Component | Pinned value |
| --- | --- |
| Application at launch | `587568c1a429d842b08bba3c747a3e9c710b4968` |
| Main framework | `340bc744ce6f7fa500d033c6f5fc67e3ec5adf62` |
| Region / instance | `us-east-1` / Spot `c8g.2xlarge` |
| AMI | `ami-072d44a0b5465abcc` |
| AMI name | `al2023-ami-2023.12.20260928.0-kernel-6.12-arm64` |
| Stockfish | `0a215d6c9e48856ef630013b8ab8312941a59057` |
| Network | `nn-134a887f4c8f.nnue` |
| Network SHA-256 | `134a887f4c8ff7bf7284177a3b3fc6ff9cef95ba89eb8db3079a8e507f7126af` |
| Compiler target | `ARCH=armv8-dotprod COMP=gcc` |
| Worker wheel SHA-256 | `3f629493119da9a6e0197860cb3635dbba8e11932c85e04fc1beb549a6b533e3` |
| AWS image digest | `sha256:62539bd040f7504a26968da7a46e16362c24c321d1381b5498b41a72bb4c52cd` |
| Prepared campaign archive SHA-256 | `5fc76163cce732aab2558f3bc3d2fdddbcd833eec0cad9c0732438facef69f9b` |

The wheel was built before the branch split; its framework implementation is
unchanged by the split. The task modules are supplied separately from the branch.
`worker-requirements.txt`, machine details, image identities, manifests, results
and raw paired samples belong with the operator evidence.

## Host setup findings

Docker save/load between the local image store and Docker 25 on AL2023 dropped
the registry name/digest association. Each VM republishes the identical image
to a registry bound only to `127.0.0.1:5000`. All three workers must resolve:

```text
localhost:5000/shinka-inference@sha256:62539bd040f7504a26968da7a46e16362c24c321d1381b5498b41a72bb4c52cd
```

This registry digest differs from the local Docker development digest because
of manifest serialization. It is recorded separately; tags alone are not used
as an evaluation identity. The loaded image configuration is unchanged.

The first preparation attempt as `ec2-user` failed when deleting files created
by the container's UID 65532. On these dedicated rootful-Docker VMs, the trusted
host coordinator runs as root, matching the production worker service. Evaluated
containers still run as `65532:65532`, without network, credentials or a Docker
socket. `--dedicated-container-vm` explicitly acknowledges this host boundary.
Ordinary developer hosts should use the documented rootless configuration.

Campaign paths are absolute in dependency manifests. The prepared archive is
unpacked at `/home/ec2-user/shinka/campaign-strict` on every worker. With the
wheel installed into `/home/ec2-user/shinka/env`, the primary command is:

```bash
cd /home/ec2-user/shinka
sudo env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONUNBUFFERED=1 \
  env/bin/python -m examples.stockfish_nnue.calibrate \
  --campaign campaign-strict --output controls-primary --freeze
```

Repeat workers use a new output directory and `--controls aa`, without `--freeze`.
They independently calibrate baseline pass counts before any candidate timings.
The campaign can freeze only after all three primary controls pass, including
per-workload A/A bias checks. Pool qualification additionally requires acceptable
independent A/A repeats. Campaigns frozen by the older aggregate-only policy
need fresh qualification; their previous success is not sufficient.

## Evidence and resource ownership

Operator files live under
`examples/stockfish_nnue/.work/aws-canary-20260930/` (excluded from Git).
`state.json` records the exact owned instances, security group and key pair.
The SSH private key stays only in that operator directory. Do not put keys,
private cases or raw private evaluation artifacts into mutation snapshots.

The VMs have encrypted disposable root disks, IMDSv2, no instance credentials,
and SSH restricted to the operator's IP. They were launched with an automatic
three-hour shutdown configured to terminate them. Collect evidence and explicitly
terminate the owned workers after qualification, then remove their temporary
security group and imported key pair. Do not alter unrelated AWS resources.

A bounded local collector (`finish-tests.py` in the operator directory) is
running for the existing primary test. It waits at most 100 minutes, downloads
and verifies `primary-evidence.tar.gz`, recomputes scores, records the failed
pool qualification, and terminates the remaining owned worker before deleting
the temporary key pair and security group. It preserves the VM for recovery if
evidence collection fails; the original three-hour termination remains active.
It submits no new evaluations and does not retry measurements.

Read `state.json` for `collector_status`, `collector_error`, cleanup timestamps
and exact resource identities. Read `primary-controls.json`, `score-audit.json`
and `workload-bias-audit.json` for the resulting operator records. The local
collector log records any failure; this handoff is a snapshot and does not
automatically change when that process finishes.
