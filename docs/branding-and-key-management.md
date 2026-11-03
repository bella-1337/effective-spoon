# Corporate branding and virtual-key management

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

## Corporate branding

The LiteLLM Admin UI logo, dark-mode logo, and favicon use LiteLLM's native
`UI_LOGO_PATH`, `UI_LOGO_PATH_DARK`, and `LITELLM_FAVICON_URL` settings. The
example `.env` points them at the placeholder Foo-Bar images in
[`branding/foo-bar`](../branding/README.md#foo-bar-example), mounted read-only into both gateways.
Replace the images, or point the variables at `https://` URLs, then recreate
the gateways:

```sh
docker compose --profile prod up -d --force-recreate litellm-prod
```

API-docs and email branding (`DOCS_*`, `EMAIL_*`) also use LiteLLM's own
variables but take effect only with a LiteLLM Enterprise licence. LiteLLM offers
no setting for the page title, login heading, colours, or fonts, so this stack
does not change them. See [`branding/README.md`](../branding/README.md) and the
[environment variable reference](environment-variables.md#corporate-branding).

## Automatic virtual-key rotation

LiteLLM 1.104 exposes per-key auto-rotation settings in the UI, but key
regeneration and scheduled rotation require a LiteLLM Enterprise license. The
current stack has no `LITELLM_LICENSE`, so the background worker is intentionally
disabled. Enabling only the UI toggle or worker would leave due rotations
failing with an Enterprise-feature error.

After obtaining a valid license, pass `LITELLM_LICENSE` securely to both proxy
services and add these stack-owned worker settings:

```yaml
LITELLM_KEY_ROTATION_ENABLED: "true"
LITELLM_KEY_ROTATION_CHECK_INTERVAL_SECONDS: "3600"
LITELLM_KEY_ROTATION_GRACE_PERIOD: 24h
```

Then use **Virtual Keys**, select a key, enable **Auto-Rotation**, and choose its
rotation interval. The worker generates a replacement key, keeps the previous
key valid for the configured grace period, and carries the rotation schedule to
the replacement.

The check interval is how often LiteLLM looks for due keys; it is not the
per-key rotation interval selected in the UI. A rotation may therefore occur up
to one check interval after its scheduled time. In a multi-replica deployment,
configure LiteLLM's supported distributed locking before relying on one
rotation execution per schedule.

### Optional email delivery for generated and rotated keys

Email is not enabled by default because this repository has no mail credentials.
LiteLLM's
[email notification documentation](https://docs.litellm.ai/docs/proxy/email)
supports SMTP, Resend, and SendGrid callbacks. For SMTP, add the callback to
`litellm_settings` in both profile configuration files:

```yaml
litellm_settings:
  callbacks: ["smtp_email"]
```

Pass these variables to each LiteLLM service from the ignored local `.env`:

```dotenv
SMTP_HOST=<smtp-host>
SMTP_TLS=True
SMTP_PORT=587
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-password>
SMTP_SENDER_EMAIL=<sender-address>

# Recommended: do not place live API keys in email.
EMAIL_INCLUDE_API_KEY=false
```

For Resend use `callbacks: ["resend_email"]` with `RESEND_API_KEY`; for
SendGrid use `callbacks: ["sendgrid_email"]` with `SENDGRID_API_KEY` and
`SENDGRID_SENDER_EMAIL`. Keep every provider secret in `.env` or a production
secrets manager, never in the YAML files.

The same callback handles both key-generation and key-rotation notifications.
For a generated key, select an owner with a valid email address and enable
**Advanced Settings > Send Email** in the Virtual Keys creation dialog. API
clients must send the equivalent request flag:

```json
{
  "user_id": "<owner-user-id>",
  "send_invite_email": true
}
```

LiteLLM does not send a key-generation message when `send_invite_email` is
omitted or false. The owner and opt-in must therefore be supplied for every key
that should trigger a notification.

The SMTP callback, key-generation event, and owner-email lookup were validated
locally with a disposable user and key while `EMAIL_INCLUDE_API_KEY=false`; the
notification was delivered to the owner's address and contained no plaintext
API key. Rotation-email delivery could not be exercised because this unlicensed
deployment cannot regenerate a virtual key.

For generated and rotated keys, the recipient is resolved from the key's
`user_id`, then from that owner's `user_email` in LiteLLM's user table. A key
without an owning user or an owner without an email address cannot receive a
notification. Team membership or the identity of the administrator who created
the key is not a substitute for explicit key ownership.

With `EMAIL_INCLUDE_API_KEY=true` (LiteLLM's upstream default), the email
contains the new plaintext key. This is convenient but exposes a credential to
mail transport, mailboxes, forwarding, retention, and account compromise. The
recommended `false` setting sends a notification with a placeholder instead;
the owner retrieves the generated or replacement key from the authenticated
LiteLLM dashboard. For rotation, the owner updates clients during the 24-hour
grace period, after which the old key is revoked.

Before enabling key email in production:

1. Confirm every notified human-owned key has the correct `user_id`, the owner
   has a verified `user_email`, and key creation sets `send_invite_email: true`.
2. Install and validate the Enterprise license before enabling rotation or its
   worker; generation notifications do not require the rotation license.
3. Use a TLS-enabled mail provider and a narrowly scoped sending credential.
4. Keep `EMAIL_INCLUDE_API_KEY=false` unless an approved risk assessment
   explicitly permits sending plaintext credentials by email.
5. Test generation with a disposable user and key and verify delivery. If
   rotation is licensed, also update a test client and confirm the previous key
   works only during the grace period.
6. Alert on rotation or email failures; do not assume the UI setting proves
   delivery.
