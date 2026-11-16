"""Contributor layer: start-here protocol, priority source, alignment engine.

Spec: docs/contrib/CONTRIBUTOR-LAYER.md. Stdlib only, plus PyYAML (already a
transitive dependency through transformers / huggingface-hub) for the YAML files
under ``.z0/``. Nothing here posts to GitHub or pushes without an explicit human
yes; the CLI holds the logic and agents only follow ``next_action``.
"""
