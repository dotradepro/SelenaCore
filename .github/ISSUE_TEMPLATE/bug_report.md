---
name: Bug report
about: Something isn't working
title: "[Bug] "
labels: bug
---

## Describe the bug

A clear and concise description of what the bug is.

## Steps to reproduce

1.
2.
3.

## Expected behavior

What you expected to happen.

## Environment

- **Hardware:** (e.g. Raspberry Pi 5, Jetson Orin Nano, generic x86_64)
- **OS:** (e.g. Ubuntu 22.04, Raspberry Pi OS Bookworm)
- **SelenaCore version:** `curl http://localhost/api/v1/system/info | jq .version`
- **Docker version:** `docker --version`

## Logs

```
docker compose logs selena-core --tail=100
```

Paste the relevant output here.

## Additional context

Screenshots, configuration snippets, or anything else that helps us reproduce the issue.
