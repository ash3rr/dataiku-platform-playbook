# Webapp pod fails with "invalid port in upstream"

Related: [webapp-404-after-upgrade.md](webapp-404-after-upgrade.md) (the later 404s, caused by duplicate vanity URLs) and [code-studios.md](../platform/code-studios.md) (why the built-in Streamlit block has no Exposed location field).

## Symptom

A code webapp deployed to Kubernetes through a Code Studio template never comes up. The deployment retries and eventually fails. The pod log shows nginx refusing its own config:

```
[emerg] invalid port in upstream "127.0.0.1:8888http://localhost:8888/" in /home/dataiku/nginx.conf:115
```

## Cause

The template has an HTTP proxy (nginx) block that exposes the app. With its Exposed location set to `/`, DSS generated an upstream line that glued the port and a full URL together (`8888http://localhost:8888/`). nginx can't parse that, the process exits, and the pod never passes its readiness check. The vendor's support team confirmed it as a known issue at the time.

## Finding the setting

The webapp's own settings only reference the template by name. The block itself lives in the template: Administration > Code Studios > the template > the HTTP proxy (nginx) block. That block has the port, one or more locations with paths (for example `/` and `/static`), and the exposed location field.

## Workaround

Set the exposed location to something other than a bare `/`. The vendor suggested `/?foo=bar`. Then rebuild the template and restart the webapp.

## Before you change it

A template change applies to every webapp and Code Studio that uses the template, as soon as they restart. There is no per-webapp override. If you don't know how many things use it:

1. Duplicate the template.
2. Apply the workaround to the copy.
3. Point the broken webapp at the copy and confirm it starts.
4. Then decide whether to change the original or move everything to the copy.
