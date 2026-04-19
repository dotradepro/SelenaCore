# Security Policy

We take the security of SelenaCore seriously. This document explains which versions are supported, how to report a vulnerability, and which areas of the codebase are in scope.

## Supported Versions

| Version              | Supported |
|----------------------|-----------|
| `main` branch        | ✅        |
| Latest tagged release| ✅        |
| Older tagged releases| ❌        |
| Forks and downstream | ❌        |

We patch the `main` branch and the most recent tagged release. Older releases will not receive security updates — please upgrade.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security reports.**

Use [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories/new) to report privately. If for any reason you cannot use the advisory flow, email the maintainers via the contact listed at <https://selenehome.tech/support>.

Include in your report:

- A clear description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal proof of concept.
- The affected SelenaCore version (`curl http://localhost/api/v1/system/info | jq .version`).
- Your environment (hardware, OS, Docker version).

### Response Timeline

| Stage              | Target                  |
|--------------------|-------------------------|
| Acknowledgement    | Within 48 hours         |
| Initial assessment | Within 5 business days  |
| Coordinated fix    | Negotiated case-by-case |

We will keep you informed throughout the process and credit you in the advisory unless you prefer to stay anonymous.

## Scope

### In scope

- Authentication bypass on the Core API or UI API.
- Module Bus token leakage or token forging.
- Integrity Agent bypass (silent core modification, SAFE MODE bypass, rollback skip).
- Secrets Vault: AES-256-GCM key/IV reuse, plaintext leakage to logs, side-channel keys exposure.
- Remote Code Execution via the module install pipeline (`/api/v1/modules/install`).
- Cross-tenant leakage via the WebSocket UI Sync (`/api/ui/sync`).
- TLS proxy (`:443`) certificate or downgrade attacks.

### Out of scope

- Attacks that require physical access to the device.
- Vulnerabilities in third-party dependencies (please report upstream — we will track and pull patches).
- Self-XSS that requires the user to paste content into devtools.
- Denial of service via legitimate API usage (rate limiting is enforced; report only if you can bypass it).

## Security Design Notes

- **Module isolation.** User modules run in Docker containers with no shared filesystem and no direct database access. Communication is restricted to the WebSocket Module Bus.
- **Module tokens.** Stored under `/secure/module_tokens/`, encrypted with AES-256-GCM. Tokens are scoped per module and revocable.
- **Integrity Agent.** A separate process verifies SHA256 of every core file every 30 seconds. On mismatch it stops modules, notifies, attempts rollback, and enters SAFE MODE if rollback fails.
- **Rate limiting.** 120 requests per 60-second window per client at the middleware layer.
- **CORS.** Restrictive by default; only the local UI origin is allowed unless explicitly extended.
- **TLS.** HTTPS on `:443` is handled by an asyncio TLS proxy (~5 MB RAM) that forwards to the unified Core process on `:80`. Certificates live in `/secure`.
- **Secrets Vault.** OAuth tokens and credentials are AES-256-GCM encrypted; modules access external APIs through a signing proxy and never see raw tokens.

## Disclosure

Once a fix is released we will publish a GitHub Security Advisory describing the vulnerability, the affected versions, the fix, and credits. Where appropriate we will request a CVE.
