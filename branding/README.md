# Corporate branding assets

LiteLLM brands its Admin UI, API docs, and notification email only through its
own environment variables. This stack sets those variables from `.env` and adds
no UI patches, injected stylesheets, or extra services.

| LiteLLM setting | Branded surface | Licence |
| --- | --- | --- |
| `UI_LOGO_PATH` | Admin UI sidebar logo (light mode) | Open source |
| `UI_LOGO_PATH_DARK` | Admin UI sidebar logo (dark mode) | Open source |
| `LITELLM_FAVICON_URL` | Admin UI browser-tab icon | Open source |
| `DOCS_TITLE`, `DOCS_DESCRIPTION` | Swagger API docs at `/` | LiteLLM Enterprise |
| `EMAIL_LOGO_URL`, `EMAIL_SUPPORT_CONTACT`, `EMAIL_SIGNATURE`, `EMAIL_SUBJECT_*` | Invitation and key emails | LiteLLM Enterprise |

`BRAND_ASSETS_DIR` is mounted read-only at `/app/branding` in both LiteLLM
containers. A logo or favicon value is either a file under `/app/branding` or
an `https://` URL. LiteLLM serves local files only when they are PNG, JPEG, GIF,
WEBP, or ICO images, and falls back to its own image otherwise. Host SVG
artwork at an `https://` URL if you need vector images.

LiteLLM has no setting for the page title, login heading, colours, or fonts, so
those keep the LiteLLM defaults.

## `foo-bar/` example

| File | Use |
| --- | --- |
| `logo.svg`, `logo-dark.svg`, `favicon.svg` | Editable sources of the placeholder FB mark. |
| `logo.png`, `logo-dark.png`, `favicon.png` | Images served by LiteLLM, rendered from the SVG sources. |

Regenerate the PNG files after editing an SVG source:

```sh
rsvg-convert -w 744 branding/foo-bar/logo.svg -o branding/foo-bar/logo.png
rsvg-convert -w 744 branding/foo-bar/logo-dark.svg -o branding/foo-bar/logo-dark.png
rsvg-convert -w 64 -h 64 branding/foo-bar/favicon.svg -o branding/foo-bar/favicon.png
```

The Foo-Bar name and FB mark are placeholders created for this repository.
Replace them with assets your organisation is licensed to use.
