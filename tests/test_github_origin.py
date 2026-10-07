from pathlib import Path

import pytest

from z0int.state_packet import _gh_slug


class OriginReader:
    def __init__(self, origin):
        self.origin = origin

    def git(self, repo, *args):
        assert args == ("remote", "get-url", "origin")
        return self.origin


@pytest.mark.parametrize("origin", [
    "https://github.com/acme/widget.git",
    "git@github.com:acme/widget.git",
    "ssh://git@github.com/acme/widget.git",
    "ssh://git@github.com:22/acme/widget.git",
    "ssh://git@ssh.github.com:443/acme/widget.git",
])
def test_github_origin_formats(origin):
    assert _gh_slug(Path("/unused"), OriginReader(origin)) == "acme/widget"


@pytest.mark.parametrize("origin", [
    "https://notgithub.com/acme/widget.git",
    "https://github.com.evil.invalid/acme/widget.git",
    "https://evil.invalid/github.com/acme/widget.git",
    "https://github.com@evil.invalid/acme/widget.git",
    "git@notgithub.com:acme/widget.git",
    "file://github.com/acme/widget.git",
    "https://github.com/acme/widget.git?ref=main",
    "https://github.com/acme/widget.git#branch",
    "ssh://git@github.com:invalid/acme/widget.git",
])
def test_noncanonical_origin_cannot_claim_github_identity(origin):
    assert _gh_slug(Path("/unused"), OriginReader(origin)) is None
