# Metadata-controlled Stockfish validation — October 5, 2026

Status: fresh native on-demand c7g.xlarge, c8g.xlarge and c7i.xlarge workers
launched. No corrected speed result is claimed before completed receipts.

This replaces the confounded October 3/4 timings. Patched dirty trees enabled
`GIT_DIFFINDEX`, unlike baseline; x86 version code and binary layout differed.
Earlier speed claims are withdrawn. All observations and checkpoints remain
preserved. The first October 4 resources were terminated and cleaned up.

The [frozen plan and worker](submission-evidence/2026-10-05/) retain the same
48-case corpus, source archive, base `49ea5ded38315cff8e67f4a677a9e7811612fbf6`,
network SHA-256 `252f33942263bc8b8f740ba8aec3fed5a159ff148113c47a55c18c33d6627ab3`,
128-block fresh-process ABBA/BAAB protocol, warmups, CPU/core isolation,
baseline-only calibration and rejection rules. All variants remain fixed.

Changes to the build preflight are declared before corrected timing:

- Explicit common `GIT_SHA=49ea5ded`, `GIT_DATE=20260930`, `GIT_DIFFINDEX=` on every
  build. Inspect actual `misc.cpp` compile lines and reject a mismatch.
- Production uses normal PGO network embedding. Stockfish's profile-build
  recursion overrides the previous requested `EXTRACXXFLAGS`; the earlier
  production builds also embedded the default network. Replay builds retain
  embedding-off flags. Every network and production bench is checksum/signature
  checked, and search uses the same pinned external EvalFile.
- On x86, full disassembly must match after stripping only objdump's file-path
  header, before any timing. Unexpected code differences stop the worker.
- Pointer-only source includes the final PR's two explanatory comments. Its
  patch is the exact engine diff of `830c5c3`; combined includes that pointer
  component plus the original lane+bank component.
- Add a fixed pointer-only deeper comparison with the matching deeper A/A.
  Thirteen comparisons per ARM worker; four on Intel. No favorable retries or
  sample trimming. Rejected controls remain inconclusive.

Workers have fourteen-hour shutdown backstops, encrypted auto-deleted volumes,
IMDSv2, operator-IP SSH and automatic private archive upload/termination. Native
builds and engines run on AWS. On-demand avoids another Spot interruption; a
separate repeat-boot pointer confirmation will follow the verified build
preflight. No Fishtest run or fitness admission is included.

Archives use private prefix `stockfish-nnue-validation/2026-10-05-metadata/`;
operator access and concrete cases stay under `.work/aws-pr-validation-20261005-metadata/`.
The original frozen plan is not rewritten, and old data is not substituted with
favorable new estimates. This is a declared correction of a build confound.
