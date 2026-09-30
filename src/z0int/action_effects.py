"""Action effects (z0int#55 enforceability layer): what a *concrete* Claude Code tool call does.

Prompts are ambiguous; tool calls are not. ``git push origin master`` names its remote and branch,
``gh pr create`` names its effect, ``ssh host sudo ...`` names its host. This module maps one tool
call (``tool_name`` + ``tool_input``) to concrete effects::

    {"class": "read"|"write"|"privileged", "kind": "push", "target": {"remote": "origin",
     "branch": "master", "protected": true}, "reason": "git push", "cmd": "git push origin master"}

Deterministic, no network, no subprocess: git state is read from ``.git`` files. Precision over
recall: a kind is claimed only when the command form is recognised; an unrecognised command is
``write`` with ``reason: unknown_command:<name>`` so the harness's standing authority decides.

Nothing here decides anything; ``action_authority.authority_check`` compares these effects with grants.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing is not imported at run time (hook latency)
    from typing import Any, Iterable, Mapping  # noqa: F401


from .shell_parse import Simple, Word, split

SCHEMA = "z0int.action_effects.v0"
ORDER = {"read": 0, "write": 1, "privileged": 2}
PROTECTED = ("main", "master", "trunk", "develop", "dev", "nightly", "production", "prod", "stable", "gh-pages")
PRIVILEGED_KINDS = ("push", "force", "merge", "commit", "delete", "discard", "rewrite", "pr", "comment", "issue",
                    "publish", "deploy", "ci", "external_system", "message", "upload", "install", "ssh", "sudo",
                    "secret", "network", "service", "fs_outside_repo")


# ----------------------------------------------------------------------------------------- git state (files only)
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


@lru_cache(maxsize=256)
def git_dirs(directory: str) -> tuple[str, str, str] | None:
    """(worktree_root, gitdir, commondir) for the repo containing ``directory``; None outside git."""
    d = os.path.abspath(directory)
    while True:
        dot = os.path.join(d, ".git")
        if os.path.isdir(dot):
            return d, dot, dot
        if os.path.isfile(dot):
            txt = _read(dot) or ""
            m = re.match(r"gitdir:\s*(.+)", txt.strip())
            if m:
                gd = m.group(1).strip()
                gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(d, gd))
                common = _read(os.path.join(gd, "commondir"))
                cd = os.path.normpath(os.path.join(gd, common.strip())) if common else gd
                return d, gd, cd
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def git_branch(directory: str) -> str | None:
    g = git_dirs(directory)
    if not g:
        return None
    head = (_read(os.path.join(g[1], "HEAD")) or "").strip()
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def _has_ref(common: str, ref: str) -> bool:
    if os.path.exists(os.path.join(common, ref)):
        return True
    packed = _read(os.path.join(common, "packed-refs")) or ""
    return (" " + ref + "\n") in packed or packed.endswith(" " + ref)


@lru_cache(maxsize=256)
def git_default_branch(directory: str) -> str | None:
    g = git_dirs(directory)
    if not g:
        return None
    sym = (_read(os.path.join(g[2], "refs", "remotes", "origin", "HEAD")) or "").strip()
    if sym.startswith("ref: refs/remotes/origin/"):
        return sym[len("ref: refs/remotes/origin/"):]
    for name in ("main", "master", "trunk"):
        if _has_ref(g[2], f"refs/remotes/origin/{name}") or _has_ref(g[2], f"refs/heads/{name}"):
            return name
    return None


def git_root(directory: str) -> str | None:
    g = git_dirs(directory)
    return g[0] if g else None


# ----------------------------------------------------------------------------------------------------- context
class Ctx:
    """Where a call runs. scope_root is the standing write scope: the session repo's toplevel (or the
    session cwd outside git). branch_of/default_of read git state (files only; replay overrides them)."""

    __slots__ = ("cwd", "scope_root", "home", "temp_roots", "branch_of", "default_of", "remote_host", "depth")

    def __init__(self, cwd: str, scope_root: str, home: str | None = None, temp_roots: tuple = (),
                 branch_of=git_branch, default_of=git_default_branch, remote_host: str | None = None, depth: int = 0):
        self.cwd = cwd
        self.scope_root = scope_root
        self.home = home or os.path.expanduser("~")
        self.temp_roots = temp_roots
        self.branch_of = branch_of
        self.default_of = default_of
        self.remote_host = remote_host  # set while parsing an ssh remote command
        self.depth = depth

    def replace(self, **kw) -> "Ctx":
        new = Ctx.__new__(Ctx)
        for k in Ctx.__slots__:
            setattr(new, k, kw.get(k, getattr(self, k)))
        return new

    @classmethod
    def for_cwd(cls, cwd: str, **kw: Any) -> "Ctx":
        root = git_root(cwd) or os.path.abspath(cwd)
        return cls(cwd=os.path.abspath(cwd), scope_root=root, **kw)

    def temps(self) -> tuple[str, ...]:
        base = (self.temp_roots or ()) + ("/tmp", "/var/tmp", "/dev", "/proc/self", os.path.join(self.home, ".cache"))
        tmpdir = os.environ.get("TMPDIR")
        return base + ((tmpdir,) if tmpdir else ())


def _is_protected(branch: str | None, default: str | None) -> bool:
    if branch is None:
        return True  # unknown target is treated as possibly protected
    b = branch.lower().removeprefix("origin/").removeprefix("refs/heads/")
    return b in PROTECTED or b == (default or "").lower() or b.startswith("release/")


def _eff(cls: str, kind: str, reason: str, cmd: str = "", **target: Any) -> dict[str, Any]:
    return {"class": cls, "kind": kind, "target": {k: v for k, v in target.items() if v is not None},
            "reason": reason, "cmd": cmd[:200]}


# ------------------------------------------------------------------------------------------------------- paths
_SECRET_NAME = re.compile(r"(?:^|/)(?:\.env(?:\.(?!example$|sample$|template$|dist$|defaults?$)[\w.-]+)?|\.netrc|\.pgpass|\.npmrc|\.pypirc"
                          r"|id_(?:rsa|dsa|ecdsa|ed25519)(?!\.pub)|[\w.-]*\.(?:pem|key|p12|pfx|keystore)|credentials(?:\.json)?"
                          r"|secrets?\.(?:json|ya?ml|toml|env)|[\w.-]*token[\w.-]*\.(?:json|txt)|hosts\.yml)$", re.I)
_SECRET_DIRS = ("/.ssh/", "/.aws/", "/.gnupg/", "/.config/gh/", "/.docker/config.json", "/.kube/config", "/.config/gcloud/")


def is_secret_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if p.endswith(".pub") or p.endswith("/known_hosts") or p.endswith("/config.example"):
        return False
    return bool(_SECRET_NAME.search(p)) or any(d in p + ("/" if not p.endswith("/") else "") or p.endswith(d.rstrip("/"))
                                              for d in _SECRET_DIRS)


def resolve(path: str, ctx: Ctx) -> str | None:
    if not path or "$" in path or "`" in path:
        return None
    if path.startswith("~/") or path == "~":
        path = ctx.home + path[1:]
    elif path.startswith("~"):
        return None
    if not os.path.isabs(path):
        path = os.path.join(ctx.cwd, path)
    return os.path.normpath(path)


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") if root != "/" else True


_HARNESS_STATE = re.compile(r"/\.claude/(?:projects/[^/]+/memory|plans|todos)(?:/|$)")


def path_effect(path_text: str, op: str, ctx: Ctx, cmd: str = "", *, dynamic: bool = False) -> dict[str, Any] | None:
    """Effect of touching a path. op: read | write | delete. None when it is plainly inside standing."""
    if ctx.remote_host:
        return None  # remote paths are covered by the ssh effect
    p = None if dynamic and "$" in path_text else resolve(path_text, ctx)
    if p is None:
        return None
    if is_secret_path(p):
        return _eff("privileged", "secret", f"{op}_secret_path", cmd, path=_short(p, ctx))
    if op == "read":
        return None
    if _under(p, ctx.scope_root) or any(_under(p, t) for t in ctx.temps()) or _HARNESS_STATE.search(p):
        return None
    if p in ("/", ctx.home) or (op == "delete" and len(p.strip("/").split("/")) <= 2):
        return _eff("privileged", "delete" if op == "delete" else "fs_outside_repo", f"{op}_catastrophic_path", cmd,
                    path=_short(p, ctx), catastrophic=True)
    return _eff("privileged", "delete" if op == "delete" else "fs_outside_repo", f"{op}_outside_repo", cmd,
                path=_short(p, ctx))


def _short(p: str, ctx: Ctx) -> str:
    return "~" + p[len(ctx.home):] if p.startswith(ctx.home + "/") or p == ctx.home else p


# --------------------------------------------------------------------------------------------------- commands
READ_ONLY = {
    "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "fd", "wc", "sort", "uniq", "cut",
    "jq", "yq", "echo", "printf", "pwd", "which", "type", "date", "whoami", "id", "uname", "stat", "file", "du", "df",
    "tree", "diff", "cmp", "sha256sum", "sha1sum", "md5sum", "b2sum", "basename", "dirname", "realpath", "readlink",
    "test", "[", "[[", "true", "false", ":", "sleep", "ps", "top", "htop", "free", "uptime", "nproc", "lscpu", "lsblk",
    "nvidia-smi", "rocm-smi", "journalctl", "hostname", "column", "tr", "xxd", "od", "strings", "seq", "wait", "hexdump",
    "bat", "nl", "fold", "fmt", "comm", "join", "paste", "expand", "rev", "tac", "look", "ss", "netstat", "lsof", "pgrep",
    "env", "printenv", "locale", "getent", "cal", "bc", "expr", "numfmt", "z0obs", "lspci", "lsusb", "ip", "ping",
    "dig", "nslookup", "host", "sensors", "vmstat", "iostat", "watch", "timeout", "command", "hash", "set", "unset",
    "export", "local", "declare", "read", "shift", "return", "exit", "break", "continue", "trap", "cd", "pushd", "popd",
    "dirs", "history", "alias", "ulimit", "umask", "shopt", "for", "case", "select", "function", "time", "fastfetch",
    "tldr", "man", "help", "info", "apropos", "whatis", "awk", "gawk", "sed", "find", "xargs", "tee", "sponge",
}
WRAPPERS = {"env", "nice", "nohup", "time", "command", "exec", "builtin", "stdbuf", "timeout", "unbuffer", "ionice",
            "taskset", "chronic", "flock", "caffeinate", "systemd-run", "script", "doas", "sudo", "if", "while",
            "until", "!", "then", "do", "else", "elif", "setsid", "strace", "ltrace", "catchsegv", "firejail", "unshare"}
_SECRET_VAR = re.compile(r"\$\{?[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*\}?")
_SECRET_WORD = re.compile(r"token|secret|passw|api_?key|credential|private_key", re.I)
_LOCALHOST = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|[\w-]+\.local(?:host)?)$", re.I)


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _strip_wrappers(argv: list[Word], ctx: Ctx, cmd: str, out: list[dict]) -> list[Word]:
    """Drop VAR=x prefixes and transparent wrappers; sudo/doas become a privileged effect and continue."""
    i = 0
    while i < len(argv):
        w = argv[i].text
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            i += 1
            continue
        b = _base(w)
        if b not in WRAPPERS:
            break
        if b in ("sudo", "doas"):
            out.append(_eff("privileged", "sudo", "sudo", cmd, host=ctx.remote_host or "local"))
            i += 1
            while i < len(argv) and argv[i].text.startswith("-"):
                opt = argv[i].text
                i += 2 if opt in ("-u", "-g", "-C", "-p", "-h", "-U", "-r", "-t", "-D") else 1
            continue
        i += 1
        if b == "env":
            while i < len(argv) and (argv[i].text.startswith("-") or "=" in argv[i].text):
                i += 2 if argv[i].text in ("-u", "-C", "-S") else 1
        elif b in ("timeout",):
            while i < len(argv) and argv[i].text.startswith("-"):
                i += 2 if argv[i].text in ("-s", "-k", "--signal", "--kill-after") else 1
            i += 1  # duration
        elif b in ("nice", "ionice", "stdbuf", "taskset", "flock", "systemd-run", "unshare", "firejail", "strace"):
            while i < len(argv) and argv[i].text.startswith("-"):
                i += 2 if argv[i].text in ("-n", "-c", "-p", "-o", "-e", "-i") else 1
            if b in ("taskset", "flock") and i < len(argv):
                i += 1
        elif b == "script":
            return []
    return argv[i:]


def _opts_positional(args: list[str], with_value: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    wv = set(with_value)
    opts, pos = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if a.startswith("-") and a != "-":
            opts.append(a)
            if a in wv and i + 1 < len(args):
                opts.append(args[i + 1])
                i += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos


# ---- git
_GIT_READ = {"status", "log", "diff", "show", "blame", "grep", "shortlog", "rev-parse", "rev-list", "ls-files", "ls-tree",
             "cat-file", "describe", "whatchanged", "name-rev", "merge-base", "for-each-ref", "show-ref", "count-objects",
             "verify-commit", "check-ignore", "check-attr", "var", "help", "version", "--version", "range-diff", "cherry",
             "difftool", "annotate", "fsck", "ls-remote", "show-branch", "log --oneline", "bisect"}
_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def _git(args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    d = ctx.cwd
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            d = resolve(args[i + 1], ctx.replace(cwd=d)) or d
        if a in _GIT_GLOBAL_WITH_VALUE:
            i += 2
            continue
        i += 1
    if i >= len(args):
        return [_eff("read", "git", "git", cmd)]
    sub, rest = args[i], args[i + 1:]
    gctx = ctx.replace(cwd=d)
    remote = ctx.remote_host
    branch = None if remote else ctx.branch_of(d)
    default = None if remote else ctx.default_of(d)
    tgt_host = {"host": remote} if remote else {}
    opts, pos = _opts_positional(rest)

    if sub in _GIT_READ:
        return [_eff("read", "git", f"git {sub}", cmd)]
    if sub == "push":
        return _git_push(rest, gctx, cmd, branch, default)
    if sub == "config":
        if any(o in opts for o in ("--get", "--get-all", "--list", "-l", "--get-regexp", "--show-origin")) or len(pos) <= 1:
            return [_eff("read", "git", "git config read", cmd)]
        if "--global" in opts or "--system" in opts:
            return [_eff("privileged", "fs_outside_repo", "git config --global", cmd, path="~/.gitconfig")]
        return [_eff("write", "git", "git config", cmd)]
    if sub == "remote":
        if not pos or pos[0] in ("show", "get-url", "-v"):
            return [_eff("read", "git", "git remote", cmd)]
        return [_eff("write", "git", f"git remote {pos[0]}", cmd)]
    if sub == "branch":
        if any(o in opts for o in ("-d", "-D", "--delete")) or re.search(r"(?:^|\s)-[a-zA-Z]*[dD]\b", " ".join(o for o in opts if not o.startswith("--"))):
            names = pos or ["?"]
            return [_eff("privileged", "delete", "git branch -d", cmd, branch=b, scope="local",
                         protected=_is_protected(b, default), **tgt_host) for b in names]
        if not pos or any(o in opts for o in ("-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--contains",
                                               "--merged", "--no-merged", "--all", "--remotes", "--points-at")):
            return [_eff("read", "git", "git branch list", cmd)]
        if any(o in opts for o in ("-f", "--force", "-M", "-C")) and pos and _is_protected(pos[0], default):
            return [_eff("privileged", "rewrite", "git branch -f on protected", cmd, branch=pos[0], protected=True)]
        return [_eff("write", "git", "git branch", cmd)]
    if sub == "tag":
        if any(o in opts for o in ("-d", "--delete")):
            return [_eff("privileged", "delete", "git tag -d", cmd, tag=t, scope="local") for t in pos or ["?"]]
        return [_eff("read" if not pos or "-l" in opts or "--list" in opts else "write", "git", "git tag", cmd)]
    if sub == "stash":
        if pos and pos[0] in ("drop", "clear"):
            return [_eff("privileged", "discard", f"git stash {pos[0]}", cmd, **tgt_host)]
        return [_eff("read" if pos and pos[0] in ("list", "show") else "write", "git", "git stash", cmd)]
    if sub == "reset":
        if "--hard" in opts or "--merge" in opts or "--keep" in opts:
            return [_eff("privileged", "discard", "git reset --hard", cmd, branch=branch,
                         protected=_is_protected(branch, default) if branch else None, **tgt_host)]
        return [_eff("write", "git", "git reset", cmd)]
    if sub == "clean":
        if any(re.match(r"^-[a-zA-Z]*n", o) or o == "--dry-run" for o in opts):
            return [_eff("read", "git", "git clean -n", cmd)]
        if any(re.match(r"^-[a-zA-Z]*f", o) or o == "--force" for o in opts):
            return [_eff("privileged", "discard", "git clean -f", cmd, **tgt_host)]
        return [_eff("write", "git", "git clean", cmd)]
    if sub in ("commit", "cherry-pick", "revert", "am", "merge", "rebase", "pull"):
        if any(o in ("--abort", "--quit", "--continue", "--skip") for o in opts):
            return [_eff("write", "git", f"git {sub} {opts[0]}", cmd)]
        if remote:
            return [_eff("write", "git", f"git {sub} (remote host)", cmd)]
        if branch and _is_protected(branch, default):
            kind = {"merge": "merge", "rebase": "rewrite", "pull": "merge"}.get(sub, "commit")
            if sub == "pull" and not any(o in ("--rebase", "-r") for o in opts) and not pos:
                # a pull on the default branch fast-forwards to its own upstream: local, reversible
                return [_eff("write", "git", "git pull on default branch", cmd)]
            return [_eff("privileged", kind, f"git {sub} on protected branch", cmd, branch=branch, protected=True,
                         repo=_short(git_root(d) or d, ctx))]
        return [_eff("write", "git", f"git {sub}" + ("" if branch else " (branch unknown)"), cmd)]
    if sub in ("filter-branch", "filter-repo"):
        return [_eff("privileged", "rewrite", f"git {sub}", cmd, branch="all", protected=True)]
    if sub == "update-ref" and ("-d" in opts):
        return [_eff("privileged", "delete", "git update-ref -d", cmd, ref=pos[0] if pos else None)]
    if sub == "worktree":
        if pos and pos[0] == "remove" and any(o in ("-f", "--force") for o in opts):
            return [_eff("privileged", "discard", "git worktree remove --force", cmd, path=pos[1] if len(pos) > 1 else None)]
        if pos and pos[0] == "list":
            return [_eff("read", "git", "git worktree list", cmd)]
        out = [_eff("write", "git", f"git worktree {pos[0] if pos else ''}".strip(), cmd)]
        if pos and pos[0] == "add" and len(pos) > 1:
            pe = path_effect(pos[1], "write", gctx, cmd)
            if pe:
                out.append(pe)
        return out
    if sub == "clone":
        out = [_eff("write", "git", "git clone", cmd)]
        _, cpos = _opts_positional(rest, ("-b", "--branch", "-o", "--origin", "--depth", "--reference", "-c", "--config",
                                          "--separate-git-dir", "--filter", "-j", "--jobs", "-u"))
        if len(cpos) > 1:
            pe = path_effect(cpos[1], "write", gctx, cmd)
            if pe:
                out.append(pe)
        return out
    return [_eff("write", "git", f"git {sub}", cmd)]


def _git_push(rest: list[str], ctx: Ctx, cmd: str, branch: str | None, default: str | None) -> list[dict[str, Any]]:
    opts, pos = _opts_positional(rest, ("-o", "--push-option", "--repo", "--receive-pack", "--exec"))
    if any(o in ("-n", "--dry-run") for o in opts):
        return [_eff("read", "git", "git push --dry-run", cmd)]
    force = any(o in ("-f", "--force", "--force-if-includes") or o.startswith("--force-with-lease") or
                (re.match(r"^-[a-zA-Z]+$", o) and "f" in o) for o in opts)
    delete = any(o in ("-d", "--delete") for o in opts)
    remote = pos[0] if pos else "origin"
    refspecs = pos[1:]
    host = {"host": ctx.remote_host} if ctx.remote_host else {}
    out: list[dict[str, Any]] = []
    if "--mirror" in opts:
        return [_eff("privileged", "force", "git push --mirror", cmd, remote=remote, branch="all", protected=True, **host)]
    if "--all" in opts or "--branches" in opts:
        out.append(_eff("privileged", "force" if force else "push", "git push --all", cmd, remote=remote, branch="all",
                        protected=True, **host))
    if "--tags" in opts or "--follow-tags" in opts:
        out.append(_eff("privileged", "publish", "git push --tags", cmd, remote=remote, tag="all", **host))
    if not refspecs and not out:
        refspecs = ["HEAD"]
    for spec in refspecs:
        plus = spec.startswith("+")
        spec = spec.lstrip("+")
        src, _, dst = spec.partition(":")
        if not _:
            dst = src
        if dst in ("HEAD", "@"):
            dst = branch if src in ("HEAD", "@", "") or src == dst else src
        if dst is None:
            dst_name = None
        else:
            dst_name = dst.removeprefix("refs/heads/")
        if dst and dst.startswith("refs/tags/") or (dst and re.match(r"^v\d+(?:\.\d+)*(?:[-.\w]*)$", dst) and dst != branch):
            out.append(_eff("privileged", "delete" if delete or (_ and not src) else "publish", "git push tag", cmd,
                            remote=remote, tag=dst.removeprefix("refs/tags/"), **host))
            continue
        prot = _is_protected(dst_name, default) if not ctx.remote_host else True
        kind = "delete" if delete or (_ and not src) else ("force" if force or plus else "push")
        out.append(_eff("privileged", kind, f"git push{' --force' if kind == 'force' else ''}", cmd, remote=remote,
                        branch=dst_name or "unknown", protected=prot, **host))
    return out


# ---- gh
_GH_READ_VERBS = {"view", "list", "status", "diff", "checks", "search", "browse", "watch", "download", "ls", "show"}


def _gh(args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    opts, pos = _opts_positional(args, ("-R", "--repo", "-B", "--base", "-H", "--head", "-t", "--title", "-b", "--body",
                                        "-F", "--body-file", "-f", "--field", "--raw-field", "-X", "--method", "-q",
                                        "--jq", "-l", "--label", "-a", "--assignee", "-m", "--milestone", "-L", "--limit",
                                        "--json", "--template", "-w", "--workflow", "-s", "--state", "-A", "--author",
                                        "--reviewer", "-r", "--input", "-H", "--header", "-p", "--paginate", "-c"))
    repo = None
    for k in ("-R", "--repo"):
        if k in opts:
            repo = opts[opts.index(k) + 1] if opts.index(k) + 1 < len(opts) else None
    if not pos:
        return [_eff("read", "gh", "gh", cmd)]
    group, verb = pos[0], (pos[1] if len(pos) > 1 else "")
    num = pos[2] if len(pos) > 2 else None
    if group == "api":
        return _gh_api(args, opts, pos, cmd)
    if group in ("search", "browse", "status", "help", "version", "completion", "alias", "config") or verb in _GH_READ_VERBS:
        if group == "release" and verb == "download":
            return [_eff("write", "gh", "gh release download", cmd)]
        return [_eff("read", "network", f"gh {group} {verb}".strip(), cmd, host="github.com")]
    if group == "pr":
        if verb == "checkout":
            return [_eff("write", "git", "gh pr checkout", cmd)]
        if verb == "merge":
            return [_eff("privileged", "merge", "gh pr merge", cmd, pr=num, repo=repo, branch="pr_base", protected=True)]
        if verb in ("comment", "review"):
            return [_eff("privileged", "comment", f"gh pr {verb}", cmd, pr=num, repo=repo)]
        base = None
        for k in ("-B", "--base"):
            if k in opts:
                base = opts[opts.index(k) + 1]
        return [_eff("privileged", "pr", f"gh pr {verb}", cmd, pr=num, repo=repo, base=base)]
    if group == "issue":
        if verb == "comment":
            return [_eff("privileged", "comment", "gh issue comment", cmd, issue=num, repo=repo)]
        if verb == "delete":
            return [_eff("privileged", "delete", "gh issue delete", cmd, issue=num, repo=repo)]
        return [_eff("privileged", "issue", f"gh issue {verb}", cmd, issue=num, repo=repo)]
    if group == "release":
        return [_eff("privileged", "delete" if verb.startswith("delete") else "publish", f"gh release {verb}", cmd,
                     tag=num, repo=repo)]
    if group == "repo":
        if verb in ("clone", "fork") and verb == "clone":
            return [_eff("write", "git", "gh repo clone", cmd)]
        if verb == "delete":
            return [_eff("privileged", "delete", "gh repo delete", cmd, repo=num or repo)]
        if verb in ("set-default",):
            return [_eff("write", "gh", "gh repo set-default", cmd)]
        return [_eff("privileged", "external_system", f"gh repo {verb}", cmd, repo=num or repo)]
    if group in ("workflow", "run", "cache"):
        return [_eff("privileged", "ci", f"gh {group} {verb}", cmd, repo=repo)]
    if group == "secret" or group == "variable":
        return [_eff("privileged", "secret", f"gh {group} {verb}", cmd, repo=repo)]
    if group == "auth":
        if verb == "status":
            return [_eff("read", "gh", "gh auth status", cmd)]
        return [_eff("privileged", "secret", f"gh auth {verb}", cmd)]
    if group in ("label", "project"):
        return [_eff("privileged", "issue", f"gh {group} {verb}", cmd, repo=repo)]
    if group == "gist":
        return [_eff("privileged", "upload" if verb in ("create", "edit") else "delete", f"gh gist {verb}", cmd)]
    if group == "extension" and verb in ("install", "upgrade"):
        return [_eff("privileged", "install", "gh extension install", cmd, env="global", packages=pos[2:3])]
    return [_eff("write", "gh", f"unknown_command:gh {group}", cmd)]


_API_KIND = ((re.compile(r"/comments\b|/reviews\b"), "comment"), (re.compile(r"/pulls/\d+/merge\b|/merges\b"), "merge"),
             (re.compile(r"/pulls\b"), "pr"), (re.compile(r"/issues\b|/labels\b"), "issue"),
             (re.compile(r"/releases\b"), "publish"), (re.compile(r"/git/refs\b"), "push"),
             (re.compile(r"/actions\b|/dispatches\b"), "ci"), (re.compile(r"/secrets\b"), "secret"))


def _gh_api(args: list[str], opts: list[str], pos: list[str], cmd: str) -> list[dict[str, Any]]:
    method = None
    for k in ("-X", "--method"):
        if k in opts:
            method = opts[opts.index(k) + 1].upper()
    fields = any(o in opts for o in ("-f", "-F", "--field", "--raw-field", "--input"))
    endpoint = pos[1] if len(pos) > 1 else ""
    if endpoint == "graphql":
        mutation = any(re.search(r"\bmutation\b", a) for a in args)
        if not mutation:
            return [_eff("read", "network", "gh api graphql query", cmd, host="api.github.com")]
        return [_eff("privileged", "external_system", "gh api graphql mutation", cmd, host="api.github.com")]
    method = method or ("POST" if fields else "GET")
    if method == "GET":
        return [_eff("read", "network", "gh api GET", cmd, host="api.github.com", endpoint=endpoint[:80])]
    kind = next((k for rx, k in _API_KIND if rx.search(endpoint)), "external_system")
    if method == "DELETE":
        kind = "delete"
    return [_eff("privileged", kind, f"gh api {method}", cmd, host="api.github.com", endpoint=endpoint[:80])]


# ---- network
_HOST_RX = re.compile(r"^(?:[a-z][\w+.-]*://)?(?:[^@/?#]*@)?(\[[^\]]*\]|[^:/?#]+)", re.I)


def _url_host(u: str) -> str | None:
    m = _HOST_RX.match(u or "")
    return m.group(1).lower().strip("[]") if m and m.group(1) else None


def _curl(b: str, args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    wv = ("-X", "--request", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form", "-T",
          "--upload-file", "-H", "--header", "-o", "--output", "-u", "--user", "-A", "--user-agent", "-e", "-m",
          "--max-time", "--connect-timeout", "-w", "--write-out", "--json", "-b", "--cookie", "-c", "--cookie-jar",
          "--retry", "-x", "--proxy", "-r", "--range", "-O", "--output-document", "--post-data", "--post-file",
          "--header", "-P", "--directory-prefix", "--unix-socket", "--cacert", "--cert", "--key", "-K", "--config")
    opts, pos = _opts_positional(args, wv if b == "curl" else ("-O", "--output-document", "--post-data", "--post-file",
                                                               "--header", "-P", "--directory-prefix", "-o", "-e", "-U",
                                                               "--user", "--password", "-T", "--timeout", "-t", "--tries"))
    method = None
    for k in ("-X", "--request"):
        if k in opts:
            method = opts[opts.index(k) + 1].upper()
    data = any(o in opts for o in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form",
                                   "-T", "--upload-file", "--json", "--post-data", "--post-file"))
    method = method or ("POST" if data else ("HEAD" if "-I" in opts or "--head" in opts else "GET"))
    urls = [p for p in pos if re.match(r"^(?:https?|ftp)://|^[\w.-]+\.[a-z]{2,}(?::\d+)?(?:/|$)|^localhost\b", p)]
    host = _url_host(urls[0]) if urls else None
    out: list[dict[str, Any]] = []
    local = bool(host and _LOCALHOST.match(host)) or (not urls and "--unix-socket" in opts)
    if method in ("GET", "HEAD"):
        out.append(_eff("read", "network", f"{b} {method}", cmd, host=host))
    elif local:
        out.append(_eff("write", "network", f"{b} {method} localhost", cmd, host=host))
    else:
        out.append(_eff("privileged", "network", f"{b} {method}", cmd, host=host or "unknown"))
    outs = [opts[i + 1] for i, o in enumerate(opts[:-1]) if o in ("-o", "--output", "--output-document", "-P")]
    if b == "wget" and "-O" in opts:
        outs.append(opts[opts.index("-O") + 1])
    for o in outs:
        pe = path_effect(o, "write", ctx, cmd)
        if pe:
            out.append(pe)
    return out


def _ssh(b: str, argv: list[Word], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    args = [w.text for w in argv[1:]]
    if b in ("ssh-keygen", "ssh-add", "ssh-copy-id"):
        if b == "ssh-keygen" and any(a in ("-l", "-lf", "-F", "-y") for a in args):
            return [_eff("read", "ssh", b, cmd)]
        return [_eff("privileged", "secret", b, cmd)]
    if b in ("scp", "rsync", "sftp"):
        hosts = []
        for a in args:
            m = re.match(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)", a)
            if m and not a.startswith("-") and not re.match(r"^[A-Za-z]:\\", a):
                hosts.append(m.group(1))
        if b == "sftp":
            pos = [a for a in args if not a.startswith("-")]
            hosts = [pos[0].split("@")[-1]] if pos else hosts
        out = [_eff("privileged", "ssh", f"{b} to remote", cmd, host=h) for h in dict.fromkeys(hosts)]
        if not hosts:  # local copy
            pos = [a for a in args if not a.startswith("-")]
            if pos:
                pe = path_effect(pos[-1], "write", ctx, cmd)
                if pe:
                    out.append(pe)
            out.append(_eff("write", "fs", f"{b} local", cmd))
        if b == "rsync" and any(a.startswith("--delete") for a in args):
            out.append(_eff("privileged", "delete", "rsync --delete", cmd, host=hosts[0] if hosts else None,
                            path=[a for a in args if not a.startswith("-")][-1:] and [a for a in args if not a.startswith("-")][-1]))
        return out
    # ssh
    with_value = {"-p", "-i", "-o", "-l", "-J", "-F", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-I", "-m", "-O", "-Q",
                  "-S", "-W", "-w", "-B"}
    i = 1
    while i < len(argv) and argv[i].text.startswith("-"):
        a = argv[i].text
        i += 2 if a in with_value else 1
    if i >= len(argv):
        return [_eff("privileged", "ssh", "ssh", cmd, host="unknown")]
    host = argv[i].text.split("@")[-1]
    remote = " ".join(w.text for w in argv[i + 1:])
    out = [_eff("privileged", "ssh", "ssh" if remote else "ssh interactive", cmd, host=host)]
    if remote:
        rctx = ctx.replace(remote_host=host, cwd="/", depth=ctx.depth + 1)
        for e in _parse_shell(remote, rctx):
            if e["class"] == "privileged" and e["kind"] != "ssh":
                e["target"]["host"] = host
                out.append(e)
    return out


# ---- installs
_PKG_MANAGERS_GLOBAL = {"brew", "apt", "apt-get", "dnf", "yum", "pacman", "paru", "yay", "snap", "flatpak", "pipx",
                        "gem", "zypper", "apk", "port", "choco", "winget", "nix-env", "pkg"}


def _pkgs(pos: list[str]) -> list[str]:
    return [re.split(r"[<>=!~\[;@ ]", p, maxsplit=1)[0].lower() for p in pos if p and not p.startswith("-")][:10]


def _install_env(interp: str | None, opts: list[str], ctx: Ctx) -> str:
    if "--user" in opts or "--system" in opts or "--break-system-packages" in opts:
        return "global"
    if interp and "/" in interp:
        p = resolve(interp, ctx)
        if p and (_under(p, ctx.scope_root) or any(_under(p, t) for t in ctx.temps())):
            return "local"
        if interp.startswith(".venv") or "/.venv/" in interp or "/venv/" in interp:
            return "local"
        if p and "/.z0int/" in p:
            return "global"
    return "unknown"


def _python_like(b: str, argv: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]] | None:
    """pip / python -m pip / uv pip / uv add ... -> install effects; None when not an install."""
    interp = None
    if re.match(r"^python[\d.]*$", b):
        if len(argv) > 2 and argv[1] == "-m" and argv[2] in ("pip", "pip3"):
            interp, rest = argv[0], argv[3:]
        else:
            return None
    elif re.match(r"^pip[\d.]*$", b):
        interp, rest = (argv[0] if "/" in argv[0] else None), argv[1:]
    else:
        return None
    opts, pos = _opts_positional(rest, ("-r", "--requirement", "-c", "--constraint", "-t", "--target", "-i", "--index-url",
                                        "--extra-index-url", "-e", "--editable", "--python", "-p", "--prefix", "--root"))
    if not pos or pos[0] not in ("install", "uninstall"):
        return [_eff("read" if pos and pos[0] in ("list", "show", "freeze", "check", "--version", "config") else "write",
                     "pip", f"pip {pos[0] if pos else ''}".strip(), cmd)]
    env = _install_env(interp, opts, ctx)
    if env == "unknown" and os.environ.get("VIRTUAL_ENV") is None and interp is None:
        env = "unknown"
    pk = _pkgs(pos[1:]) + [opts[i + 1] for i, o in enumerate(opts[:-1]) if o in ("-r", "--requirement", "-e", "--editable")]
    if env == "local":
        return [_eff("write", "install_local", f"pip {pos[0]} into repo venv", cmd, packages=pk)]
    return [_eff("privileged", "install", f"pip {pos[0]}", cmd, env=env, packages=pk)]


def _uv(args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    opts, pos = _opts_positional(args, ("-p", "--python", "--project", "--directory", "--with", "--index", "--extra",
                                        "--group", "-r", "--requirement", "--index-url", "--extra-index-url"))
    if not pos:
        return [_eff("read", "uv", "uv", cmd)]
    sub = pos[0]
    if sub == "pip":
        if len(pos) > 1 and pos[1] in ("install", "uninstall", "sync"):
            py = None
            for k in ("-p", "--python"):
                if k in opts:
                    py = opts[opts.index(k) + 1]
            env = "global" if "--system" in opts or "--user" in opts else ("local" if py is None else _install_env(py, opts, ctx))
            if env == "unknown" and py and not py.startswith("/"):
                env = "local" if re.match(r"^(?:3(?:\.\d+)?|python3?(?:\.\d+)?)$", py) is None else "unknown"
            if py and re.match(r"^(?:3(?:\.\d+)*|python3?(?:\.\d+)*)$", py):
                env = "local"  # a version selector: uv still installs into the project venv
            pk = _pkgs(pos[2:])
            if env == "local":
                return [_eff("write", "install_local", f"uv pip {pos[1]} into project venv", cmd, packages=pk)]
            return [_eff("privileged", "install", f"uv pip {pos[1]}", cmd, env=env, packages=pk)]
        return [_eff("read", "uv", f"uv pip {pos[1] if len(pos) > 1 else ''}", cmd)]
    if sub in ("add", "remove", "sync", "lock", "venv", "init", "build", "export", "tree", "version"):
        return [_eff("write", "install_local", f"uv {sub}", cmd, packages=_pkgs(pos[1:]))]
    if sub == "tool":
        verb = pos[1] if len(pos) > 1 else ""
        if verb in ("install", "upgrade", "uninstall"):
            return [_eff("privileged", "install", f"uv tool {verb}", cmd, env="global", packages=_pkgs(pos[2:]))]
        if verb == "run":
            return [_eff("write", "run_remote_package", "uv tool run", cmd, packages=_pkgs(pos[2:3]))]
        return [_eff("read", "uv", f"uv tool {verb}", cmd)]
    if sub == "python" and len(pos) > 1 and pos[1] in ("install", "uninstall"):
        return [_eff("privileged", "install", f"uv python {pos[1]}", cmd, env="global", packages=["python" + (pos[2] if len(pos) > 2 else "")])]
    if sub == "publish":
        return [_eff("privileged", "publish", "uv publish", cmd, registry="pypi")]
    if sub == "run":
        # `uv run [opts] <cmd ...>`: effects of the inner command
        inner_i = args.index("run") + 1
        inner: list[str] = []
        j = inner_i
        uv_run_wv = {"-p", "--python", "--project", "--directory", "--with", "--extra", "--group", "--env-file", "--package"}
        while j < len(args) and args[j].startswith("-"):
            j += 2 if args[j] in uv_run_wv else 1
        inner = args[j:]
        return [_eff("write", "uv", "uv run", cmd)] + (_simple_effects([Word(a) for a in inner], ctx, cmd) if inner else [])
    return [_eff("write", "uv", f"uv {sub}", cmd)]


def _node_pm(b: str, args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    opts, pos = _opts_positional(args, ("--prefix", "-C", "--dir", "--filter", "--registry", "--tag", "--access", "--otp"))
    sub = pos[0] if pos else "install"
    glob_ = any(o in ("-g", "--global", "--location=global") for o in opts) or (b == "yarn" and sub == "global")
    if sub in ("install", "i", "add", "ci", "remove", "rm", "uninstall", "un", "update", "up", "upgrade", "link", "global"):
        pk = _pkgs(pos[1:] if sub != "global" else pos[2:])
        if glob_:
            return [_eff("privileged", "install", f"{b} {sub} -g", cmd, env="global", packages=pk)]
        return [_eff("write", "install_local", f"{b} {sub}", cmd, packages=pk)]
    if sub == "publish" or (sub == "npm" and len(pos) > 1 and pos[1] == "publish"):
        return [_eff("privileged", "publish", f"{b} publish", cmd, registry="npm")]
    if sub in ("login", "adduser", "token", "logout"):
        return [_eff("privileged", "secret", f"{b} {sub}", cmd)]
    if sub in ("unpublish", "deprecate", "dist-tag", "owner", "access"):
        return [_eff("privileged", "publish", f"{b} {sub}", cmd, registry="npm")]
    if sub in ("ls", "list", "view", "info", "outdated", "audit", "why", "explain", "--version", "-v", "config", "help"):
        return [_eff("read", b, f"{b} {sub}", cmd)]
    if sub in ("exec", "dlx", "x"):
        return [_eff("write", "run_remote_package", f"{b} {sub}", cmd)]
    return [_eff("write", b, f"{b} {sub}", cmd)]


# ---- services / deploy / cloud
_CLOUD_READ = re.compile(r"^(?:get|describe|list|ls|show|logs?|top|explain|version|status|view|whoami|history|diff|"
                         r"template|plan|validate|fmt|init|output|config|api-resources|cluster-info|auth\s+can-i|inspect|search)$")


def _deploy(b: str, args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    _, pos = _opts_positional(args, ("-n", "--namespace", "--context", "-f", "--filename", "-o", "--output", "-l",
                                     "--selector", "--kubeconfig", "--region", "--project", "--profile", "-c", "--app",
                                     "-a", "--var", "--var-file", "-target"))
    verb = pos[0] if pos else ""
    ns = None
    for k in ("-n", "--namespace"):
        if k in args:
            ns = args[args.index(k) + 1] if args.index(k) + 1 < len(args) else None
    if b in ("aws", "gcloud", "az"):
        words = " ".join(pos[:4])
        if re.search(r"\b(?:describe|list|get|ls|show|wait|whoami|get-caller-identity|logs?)\b", words) and not \
                re.search(r"\b(?:create|delete|put|update|deploy|rm|cp|sync|mv|set|apply|attach|detach|start|stop|terminate|run)\b", words):
            return [_eff("read", "network", f"{b} {words}", cmd, host=b)]
        kind = "delete" if re.search(r"\b(?:delete|rm|terminate|destroy)\b", words) else "deploy"
        return [_eff("privileged", kind, f"{b} {words}", cmd, system=b)]
    if _CLOUD_READ.match(verb) or verb in ("", "--help", "-h", "help"):
        return [_eff("read" if verb not in ("init",) else "write", b, f"{b} {verb}".strip(), cmd)]
    if b in ("docker", "podman"):
        if verb == "push":
            return [_eff("privileged", "publish", f"{b} push", cmd, image=pos[1] if len(pos) > 1 else None)]
        if verb == "login":
            return [_eff("privileged", "secret", f"{b} login", cmd)]
        if verb in ("ps", "images", "logs", "inspect", "version", "info", "stats", "history", "top", "port", "events"):
            return [_eff("read", b, f"{b} {verb}", cmd)]
        if (verb == "system" and "prune" in pos) or (verb == "volume" and set(pos) & {"rm", "prune"}):
            return [_eff("privileged", "delete", f"{b} {' '.join(pos[:2])}", cmd, system=b)]
        if verb == "compose" and "down" in pos and ("-v" in args or "--volumes" in args):
            return [_eff("privileged", "delete", f"{b} compose down -v", cmd, system=b)]
        return [_eff("write", b, f"{b} {verb}", cmd)]
    if b in ("terraform", "tofu", "pulumi"):
        if verb in ("apply", "destroy", "import", "up", "state", "taint", "untaint"):
            return [_eff("privileged", "delete" if verb == "destroy" else "deploy", f"{b} {verb}", cmd, system=b)]
        return [_eff("write", b, f"{b} {verb}", cmd)]
    if b in ("kubectl", "helm", "oc", "k9s"):
        if verb in ("delete", "uninstall"):
            return [_eff("privileged", "delete", f"{b} {verb}", cmd, system=b, namespace=ns)]
        if verb in ("config",):
            return [_eff("write", b, f"{b} config", cmd)]
        if verb in ("port-forward", "proxy"):
            return [_eff("write", b, f"{b} {verb}", cmd)]
        return [_eff("privileged", "deploy", f"{b} {verb}", cmd, system=b, namespace=ns)]
    # fly / vercel / netlify / wrangler / firebase / heroku / railway
    if verb in ("deploy", "publish", "release", "promote", "rollback", "scale", "destroy", "secrets", "env") or \
            (b == "vercel" and (not pos or "--prod" in args)):
        return [_eff("privileged", "delete" if verb == "destroy" else ("secret" if verb in ("secrets", "env") else "deploy"),
                     f"{b} {verb}".strip(), cmd, system=b)]
    return [_eff("write", b, f"{b} {verb}".strip(), cmd)]


def _service(b: str, args: list[str], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    _, pos = _opts_positional(args, ("-H", "--host", "-M", "--machine", "-p", "--property", "-n", "--lines", "-o", "--output"))
    if b in ("reboot", "shutdown", "poweroff", "halt"):
        return [_eff("privileged", "service", b, cmd, unit="system", host=ctx.remote_host or "local")]
    if b == "crontab":
        if "-l" in args:
            return [_eff("read", "service", "crontab -l", cmd)]
        return [_eff("privileged", "service", "crontab", cmd, unit="crontab", host=ctx.remote_host or "local")]
    if b == "service":
        verb, units = (pos[1] if len(pos) > 1 else ""), pos[:1]
    else:
        verb, units = (pos[0] if pos else ""), pos[1:]
    if verb in ("status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "show", "cat",
                "list-timers", "list-dependencies", "get-default", "list-sockets", "help", ""):
        return [_eff("read", "service", f"{b} {verb}".strip(), cmd)]
    user = "--user" in args
    return [_eff("privileged", "service", f"{b} {verb}", cmd, unit=u, user_unit=user, host=ctx.remote_host or "local")
            for u in (units or ["?"])]


# ---- files
def _fs(b: str, argv: list[Word], ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    args = [w for w in argv[1:]]
    texts = [w.text for w in args]
    wv = {"-t", "--target-directory", "-m", "--mode", "-o", "--owner", "-g", "--group", "--suffix", "-S", "-e",
          "--expression", "-f", "--file", "-C", "-d", "--directory", "if", "--exclude"}
    pos_w: list[Word] = []
    i = 0
    only_pos = False
    tdir = None
    while i < len(args):
        a = args[i].text
        if not only_pos and a == "--":
            only_pos = True
        elif not only_pos and a.startswith("-") and a != "-":
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                tdir = args[i + 1]
            if a in wv and b not in ("rm", "mkdir", "touch", "chmod", "rmdir", "unlink", "shred") or \
                    (b == "sed" and a in ("-e", "--expression", "-f", "--file")):
                i += 1
        else:
            pos_w.append(args[i])
        i += 1
    out: list[dict[str, Any]] = []
    if b == "sed":
        if not any(t == "-i" or t.startswith("-i") or t.startswith("--in-place") for t in texts):
            return []
        script_given = any(t in ("-e", "--expression", "-f", "--file") for t in texts)
        targets = pos_w if script_given else pos_w[1:]
        op = "write"
    elif b in ("perl",):
        if not any(t.startswith("-i") or re.match(r"^-\w*i", t) for t in texts):
            return [_eff("write", "runs_code", "perl", cmd)]
        targets, op = pos_w[1:], "write"
    elif b in ("rm", "rmdir", "unlink", "shred"):
        targets, op = pos_w, "delete"
    elif b in ("mv",):
        targets = ([tdir] if tdir else pos_w[-1:]) + (pos_w if tdir else pos_w[:-1])
        op = "write"
        for src in (pos_w if tdir else pos_w[:-1]):
            pe = path_effect(src.text, "delete", ctx, cmd, dynamic=src.dynamic)
            if pe:
                out.append(pe)
        targets = [tdir] if tdir else pos_w[-1:]
    elif b in ("cp", "ln", "install", "rsync"):
        targets, op = ([tdir] if tdir else pos_w[-1:]), "write"
        for src in (pos_w if tdir else pos_w[:-1]):
            pe = path_effect(src.text, "read", ctx, cmd, dynamic=src.dynamic)
            if pe:
                out.append(pe)
    elif b in ("tee",):
        targets, op = pos_w, "write"
    elif b in ("chmod", "chown", "chgrp"):
        targets, op = pos_w[1:], "write"
    elif b in ("truncate",):
        targets, op = pos_w, "write"
    else:  # mkdir, touch
        targets, op = pos_w, "write"
    for t in targets:
        if t is None:
            continue
        pe = path_effect(t.text, op, ctx, cmd, dynamic=t.dynamic)
        if pe:
            out.append(pe)
    if not out:
        out.append(_eff("read" if b == "tee" and not targets else "write", "fs", b, cmd))
    return out


_READERS = {"cat", "head", "tail", "less", "more", "bat", "grep", "rg", "egrep", "fgrep", "nl", "xxd", "od", "strings",
            "source", ".", "jq", "yq", "awk", "diff", "cmp", "base64", "sha256sum", "md5sum"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_INTERPRETERS = re.compile(r"^(?:python[\d.]*|node|deno|bun|ruby|perl|php|lua|Rscript|julia|java|go|cargo|make|cmake|"
                           r"ninja|pytest|tox|nox|npx|pnpx|bunx|uvx|just|task|gradle|mvn|dotnet|rustc|gcc|g\+\+|clang|tsc|"
                           r"vitest|jest|mocha|ruff|black|mypy|pyright|eslint|prettier|z0int|claude|codex|hyperframes|"
                           r"ollama|llama-server|llama-cli|vllm|huggingface-cli|hf|git-lfs|tmux|pkill|kill|killall|pip-compile)$")


def _simple_effects(argv: list[Word], ctx: Ctx, cmd: str, simple: Simple | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    argv = _strip_wrappers(argv, ctx, cmd, out)
    if not argv:
        return out
    b = _base(argv[0].text)
    words = [w.text for w in argv]
    args = words[1:]
    if b in ("cd", "pushd"):
        return out  # handled by the caller
    if b in ("xargs",):
        i = 1
        while i < len(argv) and argv[i].text.startswith("-"):
            i += 2 if argv[i].text in ("-I", "-n", "-P", "-d", "-L", "-s", "-a", "-E") else 1
        return out + (_simple_effects(argv[i:], ctx, cmd) if i < len(argv) else [])
    if b == "find":
        if "-delete" in args or any(a in ("-exec", "-execdir", "-ok") for a in args):
            root = next((a for a in args if not a.startswith("-") and a not in ("(", ")", "!")), ".")
            if "-delete" in args or any(args[k + 1:k + 2] and _base(args[k + 1]) in ("rm", "shred", "unlink")
                                        for k, a in enumerate(args) if a in ("-exec", "-execdir", "-ok")):
                pe = path_effect(root, "delete", ctx, cmd)
                return out + ([pe] if pe else [_eff("write", "fs", "find -delete", cmd)])
            k = next(k for k, a in enumerate(args) if a in ("-exec", "-execdir", "-ok"))
            inner = [Word(a) for a in args[k + 1:] if a not in (";", "+", "\\;")]
            return out + _simple_effects(inner, ctx, cmd)
        return out + [_eff("read", "fs", "find", cmd)]
    if b in _SHELLS or b == "eval":
        if b == "eval":
            return out + _parse_shell(" ".join(args), ctx.replace(depth=ctx.depth + 1))
        if "-c" in args or any(re.match(r"^-\w*c$", a) for a in args):
            k = next(k for k, a in enumerate(args) if a == "-c" or re.match(r"^-\w*c$", a))
            if k + 1 < len(args):
                return out + _parse_shell(args[k + 1], ctx.replace(depth=ctx.depth + 1))
        if simple is not None and simple.heredocs:
            return out + [e for h in simple.heredocs for e in _parse_shell(h, ctx.replace(depth=ctx.depth + 1))]
        if not [a for a in args if not a.startswith("-")]:
            return out + [_eff("write", "runs_code", f"{b} (stdin script)", cmd)]
        return out + [_eff("write", "runs_code", f"{b} script", cmd)]
    if b == "git":
        return out + _git(args, ctx, cmd)
    if b == "gh":
        return out + _gh(args, ctx, cmd)
    if b in ("ssh", "scp", "rsync", "sftp", "ssh-keygen", "ssh-add", "ssh-copy-id", "mosh"):
        if b == "rsync" and not any(re.match(r"^(?:[\w.-]+@)?[\w.-]+:(?!//)", a) for a in args if not a.startswith("-")):
            return out + _fs("rsync", argv, ctx, cmd) + ([_eff("privileged", "delete", "rsync --delete", cmd,
                                                                path=args[-1])] if any(a.startswith("--delete") for a in args)
                                                        and path_effect(args[-1], "delete", ctx, cmd) else [])
        return out + _ssh("ssh" if b == "mosh" else b, argv, ctx, cmd)
    if b in ("curl", "wget", "http", "xh", "httpie"):
        return out + _curl("curl" if b != "wget" else "wget", args, ctx, cmd)
    pyi = _python_like(b, words, ctx, cmd)
    if pyi is not None:
        return out + pyi
    if b in ("uv",):
        return out + _uv(args, ctx, cmd)
    if b in ("npm", "pnpm", "yarn", "bun") and args[:1] != ["run"] and not (b == "bun" and args[:1] and args[0].endswith((".ts", ".js"))):
        if b == "bun" and args[:1] in (["test"], ["x"]):
            return out + [_eff("write", "bun", "bun " + args[0], cmd)]
        return out + _node_pm(b, args, ctx, cmd)
    if b == "cargo":
        sub = args[0] if args else ""
        if sub == "install":
            return out + [_eff("privileged", "install", "cargo install", cmd, env="global", packages=_pkgs(args[1:]))]
        if sub == "publish":
            return out + [_eff("privileged", "publish", "cargo publish", cmd, registry="crates.io")]
        return out + [_eff("write", "cargo", f"cargo {sub}", cmd)]
    if b == "go" and args[:1] == ["install"]:
        return out + [_eff("privileged", "install", "go install", cmd, env="global", packages=_pkgs(args[1:]))]
    if b in ("gem",) and args[:1] == ["install"] or b in _PKG_MANAGERS_GLOBAL:
        sub = args[0] if args else ""
        if b in ("pacman", "paru", "yay"):
            if any(re.match(r"^-[QF]", a) or re.match(r"^-S\w*[si]", a) for a in args):
                return out + [_eff("read", b, f"{b} query", cmd)]
            if not any(re.match(r"^-[SRU]", a) for a in args) and b == "pacman":
                return out + [_eff("read", b, f"{b}", cmd)]
            return out + [_eff("privileged", "install", f"{b} {sub}", cmd, env="global",
                               packages=_pkgs([a for a in args if not a.startswith("-")]))]
        if sub in ("list", "search", "info", "show", "outdated", "--version", "doctor", "config", "policy", "query", "ls"):
            return out + [_eff("read", b, f"{b} {sub}", cmd)]
        return out + [_eff("privileged", "install", f"{b} {sub}", cmd, env="global", packages=_pkgs(args[1:]))]
    if b in ("docker", "podman", "kubectl", "helm", "oc", "terraform", "tofu", "pulumi", "fly", "flyctl", "vercel",
             "netlify", "wrangler", "firebase", "heroku", "railway", "aws", "gcloud", "az"):
        return out + _deploy(b, args, ctx, cmd)
    if b in ("systemctl", "service", "launchctl", "reboot", "shutdown", "poweroff", "halt", "crontab"):
        return out + _service(b, args, ctx, cmd)
    if b in ("rm", "rmdir", "unlink", "shred", "mv", "cp", "ln", "install", "mkdir", "touch", "chmod", "chown", "chgrp",
             "tee", "truncate", "sed", "perl"):
        fx = _fs(b, argv, ctx, cmd)
        if b == "sed" and not fx:
            fx = [_eff("read", "fs", "sed", cmd)]
        return out + fx
    if b in ("echo", "printf"):
        if any(_SECRET_VAR.search(w.text) for w in argv[1:]) or any(_SECRET_VAR.search(a) for a in args):
            return out + [_eff("privileged", "secret", f"{b} of a secret variable", cmd)]
        return out + [_eff("read", "shell", b, cmd)]
    if b == "printenv" and any(_SECRET_WORD.search(a) for a in args):
        return out + [_eff("privileged", "secret", "printenv secret", cmd)]
    if b in _READERS:
        fx = []
        for w in argv[1:]:
            if not w.text.startswith("-") and is_secret_path(w.text) and not w.dynamic:
                fx.append(_eff("privileged", "secret", f"{b} reads a secret path", cmd, path=w.text))
        if b in ("source", "."):
            return out + (fx or [_eff("write", "runs_code", "source", cmd)])
        return out + (fx or [_eff("read", "fs", b, cmd)])
    if b in READ_ONLY:
        return out + [_eff("read", "shell", b, cmd)]
    if b in ("tar", "unzip", "7z", "zip", "gzip", "gunzip", "xz", "zstd"):
        dest = None
        for k in ("-C", "--directory", "-d"):
            if k in args and args.index(k) + 1 < len(args):
                dest = args[args.index(k) + 1]
        pe = path_effect(dest, "write", ctx, cmd) if dest else None
        return out + ([pe] if pe else [_eff("write", "fs", b, cmd)])
    if b in ("dd",):
        of = next((a[3:] for a in args if a.startswith("of=")), None)
        pe = path_effect(of, "write", ctx, cmd) if of else None
        return out + ([pe] if pe else [_eff("write", "fs", "dd", cmd)])
    if b in ("kill", "pkill", "killall"):
        return out + [_eff("write", "process", b, cmd)]
    if _INTERPRETERS.match(b) or argv[0].text.startswith(("./", "/", "~/", ".venv/", "bin/", "scripts/")):
        return out + [_eff("write", "runs_code", b, cmd)]
    return out + [_eff("write", "unknown", f"unknown_command:{b[:40]}", cmd)]


def _redir_effects(simple: Simple, ctx: Ctx, cmd: str) -> list[dict[str, Any]]:
    out = []
    for op, target in simple.redirs:
        if op in (">", ">>", "&>", "&>>", ">|", "<>"):
            pe = path_effect(target.text, "write", ctx, cmd, dynamic=target.dynamic)
            if pe:
                out.append(pe)
        elif op == "<" and is_secret_path(target.text):
            out.append(_eff("privileged", "secret", "stdin from a secret path", cmd, path=target.text))
    return out


_SECRET_FILTER = re.compile(r"token|secret|passw|api.?key|credential|private", re.I)


def _parse_shell(command: str, ctx: Ctx) -> list[dict[str, Any]]:
    if ctx.depth > 5:
        return [_eff("write", "unknown", "nesting_too_deep", command)]
    simples = split(command)
    out: list[dict[str, Any]] = []
    cwd = ctx.cwd
    prev_words: list[str] = []
    for s in simples:
        if not s.argv:
            out.extend(_redir_effects(s, ctx.replace(cwd=cwd), command))
            continue
        words = s.words
        text = " ".join(words)
        # effective cwd tracking (top-level, sequential)
        stripped = [w for w in words if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
        if stripped and stripped[0] in ("cd", "pushd") and not s.nested:
            dest = next((w for w in stripped[1:] if not w.startswith("-")), "~")
            dyn = any(w.dynamic for w in s.argv[1:])
            new = None if dyn else resolve(dest, ctx.replace(cwd=cwd))
            cwd = new if new else "/__unknown__"
            continue
        sctx = ctx.replace(cwd=cwd)
        fx = _simple_effects(list(s.argv), sctx, text, s)
        # curl ... | sh : remote code execution is an install
        if s.joined_by in ("|", "|&") and stripped and _base(stripped[0]) in _SHELLS | {"python", "python3", "sudo"} and \
                prev_words and _base(prev_words[0]) in ("curl", "wget"):
            host = next((_url_host(w) for w in prev_words[1:] if w.startswith("http")), None)
            fx.append(_eff("privileged", "install", "pipe remote script to a shell", text, env="global", host=host,
                           packages=["remote-script"]))
        # env | grep TOKEN : secret exposure
        if s.joined_by in ("|", "|&") and prev_words and _base(prev_words[0]) in ("env", "printenv", "set") and \
                _SECRET_FILTER.search(text):
            fx.append(_eff("privileged", "secret", "environment filtered for secrets", text))
        fx.extend(_redir_effects(s, sctx, text))
        out.extend(fx)
        prev_words = stripped
    if not out:
        out.append(_eff("read", "shell", "empty", command))
    return out


# ------------------------------------------------------------------------------------------------ tool calls
_MCP_READ = re.compile(r"^(?:get|list|search|read|query|fetch|find|view|describe|whoami|export|guide|inspect|check|status|"
                       r"lookup|browse|resolve|preview|count|diff|validate|analy[sz]e|summari[sz]e|explain|screenshot|"
                       r"download|metadata)", re.I)
_MCP_SECRET = re.compile(r"auth|login|token|credential", re.I)
_MCP_KIND = ((re.compile(r"delete|remove|destroy|trash|purge|drop", re.I), "delete"),
             (re.compile(r"send|reply|post_message|message|email|dm\b|chat", re.I), "message"),
             (re.compile(r"comment", re.I), "comment"),
             (re.compile(r"publish|upload|share|release", re.I), "publish"),
             (re.compile(r"merge", re.I), "merge"), (re.compile(r"push", re.I), "push"),
             (re.compile(r"deploy", re.I), "deploy"))
_MCP_WRITE = re.compile(r"create|update|delete|remove|send|post|publish|upload|write|set|add|edit|merge|push|comment|"
                        r"reply|close|archive|move|rename|share|invite|execute|run|trigger|deploy|apply|batch|use|"
                        r"generate|insert|modify|patch|put|submit|approve|assign|schedule|cancel|transfer|pay|order|trade", re.I)
LOCAL_MCP_SERVERS = ("z0intelligence", "pencil", "plugin_z0intelligence_z0intelligence")


def _mcp(tool: str, tool_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    parts = tool.split("__")
    server = parts[1] if len(parts) > 1 else "?"
    name = parts[-1] if len(parts) > 2 else tool
    server_n = re.sub(r"^claude_ai_", "", server)
    if any(server.endswith(s) for s in LOCAL_MCP_SERVERS):
        return [_eff("read" if _MCP_READ.match(name) else "write", "mcp_local", f"mcp local {name}", tool, server=server_n)]
    if _MCP_SECRET.search(name):
        return [_eff("privileged", "secret", f"mcp {name}", tool, server=server_n)]
    if _MCP_READ.match(name) and not re.match(r"^(?:get_or_create|find_and)", name):
        return [_eff("read", "network", f"mcp {name}", tool, server=server_n)]
    if _MCP_WRITE.search(name):
        kind = next((k for rx, k in _MCP_KIND if rx.search(name)), "external_system")
        return [_eff("privileged", kind, f"mcp {name}", tool, server=server_n, tool=name)]
    return [_eff("write", "mcp", f"unknown_mcp_tool:{name}", tool, server=server_n)]


def parse_tool_call(tool_name: str, tool_input: Mapping[str, Any] | None, ctx: Ctx) -> dict[str, Any]:
    """Concrete effects of one Claude Code tool call. Never raises: an internal error is a write effect."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    try:
        effects = _tool_effects(tool_name or "", tool_input, ctx)
    except Exception as exc:  # pragma: no cover - defensive; precision over recall
        effects = [_eff("write", "unknown", f"parser_error:{type(exc).__name__}", str(tool_name))]
    if not effects:
        effects = [_eff("read", "tool", tool_name or "?", "")]
    # de-duplicate identical effects
    seen, uniq = set(), []
    for e in effects:
        key = (e["class"], e["kind"], tuple(sorted((k, str(v)) for k, v in e["target"].items())))
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    cls = max((e["class"] for e in uniq), key=ORDER.__getitem__)
    return {"schema": SCHEMA, "tool": tool_name, "effect_class": cls, "effects": uniq,
            "privileged": [e for e in uniq if e["class"] == "privileged"]}


_FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_READ_TOOLS = {"Read": "file_path", "Glob": "path", "Grep": "path", "LS": "path", "NotebookRead": "notebook_path"}
_LOCAL_TOOLS = {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop", "TaskOutput", "ScheduleWakeup",
                "SendMessage", "EnterWorktree", "ExitWorktree", "EnterPlanMode", "ExitPlanMode", "KillShell", "BashOutput",
                "CronCreate", "CronDelete", "CronList", "PushNotification", "RemoteTrigger"}
_READ_ONLY_TOOLS = {"ToolSearch", "Skill", "ListAgents", "SubagentHandback", "AskUserQuestion", "ListMcpResourcesTool",
                    "ReadMcpResourceTool", "LSP", "WebSearch"}


def _tool_effects(tool: str, ti: Mapping[str, Any], ctx: Ctx) -> list[dict[str, Any]]:
    if tool == "Bash" or (tool == "Monitor" and isinstance(ti.get("command"), str)):
        cmd = ti.get("command") or ""
        return _parse_shell(cmd, ctx) if isinstance(cmd, str) else [_eff("write", "unknown", "bash_without_command", "")]
    if tool in _FILE_TOOLS:
        p = ti.get(_FILE_TOOLS[tool]) or ""
        pe = path_effect(str(p), "write", ctx, f"{tool} {p}")
        return [pe] if pe else [_eff("write", "fs", tool, f"{tool} {p}")]
    if tool in _READ_TOOLS:
        p = str(ti.get(_READ_TOOLS[tool]) or "")
        if p and is_secret_path(resolve(p, ctx) or p):
            return [_eff("privileged", "secret", f"{tool} of a secret path", f"{tool} {p}", path=p)]
        return [_eff("read", "fs", tool, f"{tool} {p}")]
    if tool == "WebFetch":
        url = str(ti.get("url") or "")
        return [_eff("read", "network", "WebFetch", f"WebFetch {url[:120]}", host=_url_host(url))]
    if tool in ("Agent", "Task"):
        return [_eff("read", "spawn_agent", "subagent (its own tool calls are checked separately)", tool,
                     agent=str(ti.get("subagent_type") or "general-purpose"))]
    if tool == "Artifact":
        action = ti.get("action") or "publish"
        if action in ("read", "list", "open", "quickstart"):
            return [_eff("read", "network", f"Artifact {action}", tool, host="claude.ai")]
        if action == "delete":
            return [_eff("privileged", "delete", "Artifact delete", tool, host="claude.ai")]
        if action in ("pin", "unpin"):
            return [_eff("write", "artifact", f"Artifact {action}", tool)]
        return [_eff("privileged", "upload", "Artifact publish (private page)", tool, host="claude.ai")]
    if tool in ("ArtifactData", "ArtifactComments"):
        action = str(ti.get("action") or "")
        if action in ("get", "list", "query", "read", "watch", ""):
            return [_eff("read", "network", f"{tool} {action}", tool, host="claude.ai")]
        return [_eff("privileged", "comment" if tool == "ArtifactComments" else "external_system", f"{tool} {action}",
                     tool, host="claude.ai")]
    if tool.startswith("mcp__"):
        return _mcp(tool, ti)
    if tool in _READ_ONLY_TOOLS:
        return [_eff("read", "tool", tool, tool)]
    if tool in _LOCAL_TOOLS or tool == "Monitor":
        return [_eff("write", "tool", tool, tool)]
    return [_eff("write", "unknown", f"unknown_tool:{tool[:40]}", tool)]
