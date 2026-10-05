# Corrected build preflight and fixed comparisons

The plan and original worker text freeze the metadata-controlled replacement
before timing. [The run record](../../AWS_PR_VALIDATION_2026-10-05.md) describes
why the October 3/4 timing claims are withdrawn. Those attempts remain archived.

The same corpus is reproducible from the [October 4 recipe](../2026-10-04/README.md)
and normalized case digest. Pointer source now matches PR commit `830c5c3`
including comments. Common build metadata is asserted from actual compile
commands, and x86 code identity must pass before measurement. Runtime data is
published only from completed receipts. No Fishtest or Elo result is claimed.

The Intel [code-identity guard](x86-guard.json) failed before timing. ARM workers
were stopped before timing. [All archives were collected and owned resources
cleaned up](attempt.json). The [common-profile replacement](../2026-10-05-common-profile/)
is declared separately.
