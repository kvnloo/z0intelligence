<!--
See CONTRIBUTING.md. The `receipt` check reads the yaml block below.
Push first, then put `git rev-parse HEAD` in head_revision. Editing this
description re-runs the check. Every new push needs a new head_revision.
-->

## What and why

Refs #<issue>

## Evidence

```yaml
issue: <issue number or task id>
base_revision: <merge-base sha>
head_revision: <PR head sha>
tests:
  red: "<command + result before the change>"
  green: "<command + result on the head sha>"
```

- [ ] I commented `/claim` on the issue and a maintainer confirmed it.
- [ ] No personal data (profile, paths, credentials, transcripts) is in this PR.
