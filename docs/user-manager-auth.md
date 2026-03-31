# Authentication, User Management, and Security

## Module Token Authentication

- All API requests require `Authorization: Bearer <token>`
- Tokens stored in `/secure/module_tokens/*.token` files
- Each module gets its own token at installation
- Dev token: set `DEV_MODULE_TOKEN` env var for testing

## Security Model

- **iptables:** UI routes (`/api/ui/*`) accessible only from localhost
- **API routes** (`/api/v1/*`) require Bearer token
- Modules cannot access `/secure/` directory directly
- `core.*` events can only be published by core
- Module Bus ACL enforces permissions per module type

## Rate Limiting

- 120 requests per 60 seconds per client
- Configurable in `RateLimitMiddleware`

## User Profiles (user_manager system module)

- Multiple user profiles supported
- PIN authentication (4-8 digits)
- PIN brute-force protection: 5 attempts → 10 minute lockout

## Biometric Authentication (local-only)

- Face ID via camera (face recognition)
- Speaker ID via voice print (resemblyzer)
- All processing local, no cloud services
- Biometric data stored in `/secure/`

## Secrets Vault (secrets_vault system module)

- AES-256-GCM encryption
- Stores OAuth tokens, API keys
- Located in `/secure/tokens/`
- API: `POST /api/v1/secrets`, `GET /api/v1/secrets`
- Modules access via API, never directly reading `/secure/`

## Audit Log

- All user actions logged to SQLite `audit_log` table
- 10,000 records with automatic rotation
- Fields: `action`, `user_id`, `timestamp`, `details`
- Query via user_manager system module API

## OAuth Integration

- Google OAuth for account linking
- Tuya OAuth for smart device integration
- Credentials in `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, etc.

## Tailscale VPN (remote_access system module)

- Secure remote access via Tailscale
- Auth key in `.env`: `TAILSCALE_AUTH_KEY`
- No port forwarding needed
