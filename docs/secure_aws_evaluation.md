# Secure AWS evaluation

`SecureJobConfig.backend: aws` sends the same immutable candidate, evaluator,
dependencies and environment contract used by local secure evaluation to an S3/SQS
worker pool. The evaluator supplies the task and score; the transport has no
application-specific scoring or test data.

Add the following to an otherwise configured secure task:

```yaml
job:
  backend: aws
  aws_region: us-east-1
  aws_bucket: YOUR_PRIVATE_ARTIFACT_BUCKET
  aws_queue_url: YOUR_FIFO_QUEUE_URL
  aws_prefix: shinka
  aws_job_timeout_seconds: 7200
  queue_timeout_seconds: 7200
  mutation_dependency_scope: runtime
  cpu_set: '2'
max_evaluation_jobs: 32
```

Use digest-pinned build/runtime images and matching worker architectures. CPU
affinity is optional; when specified, container inspection verifies it. Remote
evaluation concurrency is independent of the proposer's local CPU count. The
dedicated Linux worker holds a host-wide lock through compilation and evaluation.
Run it with `python -m shinka.secure.aws_worker --help` for its configuration.
Provision the private bucket, FIFO queue, permissions and workers separately.

S3 stores artifacts by content digest. Independent state directories receive
independent run identities; resuming the same state preserves job identity. SQS
messages contain job IDs, with visibility heartbeats and finite execution limits.
Duplicate delivery is safe; first terminal publication is conditional. Host loss
can rerun work, so this is not exactly-once execution. Whole failed attempts must
be discarded when measurements require uninterrupted execution.

Only the trusted host uses AWS credentials. Candidate containers have no network,
host credentials or Docker socket. Public results contain only allowlisted
metrics and enabled public feedback. Operator diagnostics stay private.

Secure failed, missing or malformed measurements are recorded in the attempt log
and `evaluation_failure.json`, never as scored Program rows. They do not update
model rewards, prompt fitness or selection. Valid `correct=false` evaluations
still represent rejected candidates. Seed initialization requires a successfully
measured, correct result. Already-spent proposal costs survive restart, including
failed measurements, and a population containing only generation zero resumes
without recreating its seed.
