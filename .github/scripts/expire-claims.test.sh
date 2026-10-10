#!/usr/bin/env bash
# Offline check for expire-claims.sh. Uses a stand-in gh, so it needs no token
# and makes no GitHub calls. Usage: bash .github/scripts/expire-claims.test.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cat >"$work/gh" <<'STUB'
#!/usr/bin/env bash
case "$1 $2" in
  "issue list") printf '%s\n' 11 12 13 ;;
  "api repos/{owner}/{repo}/issues/11/comments") echo '[{"body":"Claiming for a\nexpires_at: 2020-01-01T00:00:00Z","createdAt":"2019-12-31T00:00:00Z"}]' ;;
  "api repos/{owner}/{repo}/issues/12/comments") echo '[{"body":"Claiming for b\nexpires_at: 2999-01-01T00:00:00Z","createdAt":"2020-01-01T00:00:00Z"}]' ;;
  "api repos/{owner}/{repo}/issues/13/comments") echo '[{"body":"just a note","createdAt":"2020-01-01T00:00:00Z"}]' ;;
  "issue edit"|"issue comment") echo "$*" >>"$GH_STUB_LOG" ;;
  *) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac
STUB
chmod +x "$work/gh"
export GH_STUB_LOG="$work/writes.log"
: >"$GH_STUB_LOG"

fail() { echo "not ok - $1" >&2; exit 1; }

out="$(PATH="$work:$PATH" bash "$here/expire-claims.sh" --dry-run)" || fail "dry run exited non-zero"
grep -q '^expire #11 ' <<<"$out" || fail "an expired lease is reported"
grep -q '^keep #12 (expires_at [0-9]* > now ' <<<"$out" || fail "a live lease is kept, with a reason that reads true"
grep -q '^keep #13 (no claim timestamp)' <<<"$out" || fail "an issue with no claim is kept"
grep -q '^released 1 lease' <<<"$out" || fail "exactly one lease is counted"
[[ ! -s "$GH_STUB_LOG" ]] || fail "dry run writes nothing"
echo "ok - dry run reads the piped comments and writes nothing"

PATH="$work:$PATH" bash "$here/expire-claims.sh" >/dev/null || fail "real run exited non-zero"
grep -q '^issue edit 11 --remove-label claimed --add-label claimable$' "$GH_STUB_LOG" || fail "the expired issue is relabelled"
[[ "$(grep -c '^issue comment 11 ' "$GH_STUB_LOG")" -eq 1 ]] || fail "the expired issue gets one comment"
! grep -qE '^issue (edit|comment) 1[23] ' "$GH_STUB_LOG" || fail "live and unclaimed issues are untouched"
echo "ok - only the expired lease is released"
