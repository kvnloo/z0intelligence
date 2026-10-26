<!--
Contributors: `z0int contribute pr <task-id>` writes this body for you, with the
receipts filled in. See CONTRIBUTING.md. PRs target `master` from your fork.
-->

## Task

Task id: `<task-id from .z0/priorities.yaml>`
Refs #<issue>

Exit criterion (from the task):

## Receipts (exact head)

| step | command | exit | result | sha | output sha256 |
|---|---|---|---|---|---|
| S6 verifier + product smoke | `z0int contribute evidence <task-id>` |  |  |  |  |

Hardware class: `cpu | <gpu>-<vram>g`
Harness / model:

Credit: @<your-login>

```yaml
issue: <issue number or task id>
base_revision: <merge-base sha>
head_revision: <PR head sha>
tests:
  red: "<command + result before the change>"
  green: "<command + result on the head sha>"
```

- [ ] I have read and agree to the z0intelligence Contributor License Agreement (the CLA bot checks this PR).
- [ ] The claim for an `exclusive` task is posted and active (`/claim` on the issue).
- [ ] No personal data (profile, paths, credentials, transcripts) is in this PR.
