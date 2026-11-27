<!-- human-version-of: docs/branding-and-key-management.md sha256: bc66ef5d04a6b700f19c4894092b485d9d48cc23fbbc04363a5b20862a419c66 -->

# Corporate branding and virtual-key management

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page shows how to put your own logo and favicon on the LiteLLM Admin UI,
and what it takes to rotate virtual keys automatically and email them to their
owners. Read it when you brand a deployment or plan key rotation.

## Corporate branding

The LiteLLM Admin UI logo, dark-mode logo, and favicon use LiteLLM's native
settings:

- `UI_LOGO_PATH`
- `UI_LOGO_PATH_DARK`
- `LITELLM_FAVICON_URL`

The example `.env` points them at the placeholder Foo-Bar images in
[`branding/foo-bar`](../branding/README.md#foo-bar-example), mounted read-only
into both gateways.

To change them:

1. Replace the images, or point the variables at `https://` URLs.
2. Recreate the gateways:

```sh
docker compose --profile prod up -d --force-recreate litellm-prod
```

!!! tip "The favicon does not change"
    Browsers cache favicons aggressively. After changing it, hard-reload the
    Admin UI or open it in a private window.

??? note "How the favicon reaches the browser"
    LiteLLM serves the favicon at `/get_favicon`, but its Admin UI also links
    its bundled `/favicon.ico`, and browsers request that path on their own.
    Traefik therefore sends `/favicon.ico` on both gateway hosts to
    `/get_favicon` (the `litellm-favicon` middleware in
    `traefik/dynamic.yml`), so every browser shows the configured favicon.

### What you cannot brand

- API-docs and email branding (`DOCS_*`, `EMAIL_*`) also use LiteLLM's own
  variables, but take effect only with a LiteLLM Enterprise licence.
- LiteLLM offers no setting for the page title, login heading, colours, or
  fonts, so this stack does not change them.

See [`branding/README.md`](../branding/README.md) and the
[environment variable reference](environment-variables.md#corporate-branding).

## Automatic virtual-key rotation

LiteLLM 1.104 exposes per-key auto-rotation settings in the UI. Key
regeneration and scheduled rotation, however, require a LiteLLM Enterprise
license.

The current stack has no `LITELLM_LICENSE`, so the background worker is
intentionally disabled. Enabling only the UI toggle or the worker would leave
due rotations failing with an Enterprise-feature error.

### Turn on rotation

1. Obtain a valid license.
2. Pass `LITELLM_LICENSE` securely to both proxy services.
3. Add these stack-owned worker settings:

    ```yaml
    LITELLM_KEY_ROTATION_ENABLED: "true"
    LITELLM_KEY_ROTATION_CHECK_INTERVAL_SECONDS: "3600"
    LITELLM_KEY_ROTATION_GRACE_PERIOD: 24h
    ```

4. Open **Virtual Keys**, select a key, enable **Auto-Rotation**, and choose
   its rotation interval.

The worker then:

- generates a replacement key;
- keeps the previous key valid for the configured grace period; and
- carries the rotation schedule to the replacement.

### Check interval and rotation interval

The check interval is how often LiteLLM looks for due keys. It is not the
per-key rotation interval you select in the UI. A rotation may therefore occur
up to one check interval after its scheduled time.

In a multi-replica deployment, configure LiteLLM's supported distributed
locking before you rely on one rotation execution per schedule.

### Optional email delivery for generated and rotated keys

Email is not enabled by default, because this repository has no mail
credentials. LiteLLM's
[email notification documentation](https://docs.litellm.ai/docs/proxy/email)
supports SMTP, Resend, and SendGrid callbacks.

#### Configure the mail callback

For SMTP, add the callback to `litellm_settings` in both profile configuration
files:

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

For other providers:

| Provider | Callback | Variables |
| --- | --- | --- |
| Resend | `callbacks: ["resend_email"]` | `RESEND_API_KEY` |
| SendGrid | `callbacks: ["sendgrid_email"]` | `SENDGRID_API_KEY` and `SENDGRID_SENDER_EMAIL` |

Keep every provider secret in `.env` or a production secrets manager, never in
the YAML files.

#### Who gets the email

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
omitted or false. So you must supply the owner and the opt-in for every key
that should trigger a notification.

For generated and rotated keys, LiteLLM finds the recipient in two steps:

1. the key's `user_id`;
2. that owner's `user_email` in LiteLLM's user table.

A key without an owning user, or an owner without an email address, cannot
receive a notification. Team membership, or the identity of the administrator
who created the key, does not replace explicit key ownership.

#### Plaintext keys in email

With `EMAIL_INCLUDE_API_KEY=true` (LiteLLM's upstream default), the email
contains the new plaintext key. This is convenient, but it exposes a credential
to mail transport, mailboxes, forwarding, retention, and account compromise.

The recommended `false` setting sends a notification with a placeholder
instead. The owner retrieves the generated or replacement key from the
authenticated LiteLLM dashboard. For rotation, the owner updates clients during
the 24-hour grace period, after which the old key is revoked.

#### What has been tested

The SMTP callback, key-generation event, and owner-email lookup were validated
locally with a disposable user and key while `EMAIL_INCLUDE_API_KEY=false`. The
notification was delivered to the owner's address and contained no plaintext
API key.

Rotation-email delivery could not be exercised, because this unlicensed
deployment cannot regenerate a virtual key.

#### Before you enable key email in production

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
