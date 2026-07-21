# Provider egress proxy

This small CONNECT proxy is the trusted egress half of Shinka's
`provider_only` network. It fails closed unless `ALLOWED_HOST_SUFFIXES` is set,
accepts HTTPS port 443 only, rejects literal IP destinations, and refuses DNS
answers in private or special-use ranges.

Attach the proxy to both the labeled internal provider network and a separate
egress-capable network. Mutation containers join only the internal network and
use `http://<proxy-name>:8080` as `evo.agent_provider_proxy`.

For Antigravity, start with `googleapis.com`; for Claude, Codex, Cursor, Gemini,
OpenCode, or Pi, derive the smallest suffix set from the selected native agent's
documented endpoints. When a run selects multiple agents, use only the reviewed
union. Add `models.dev` only when Headless must price token usage that the native
agent did not price itself. Never add a wildcard or generic Internet suffix.
