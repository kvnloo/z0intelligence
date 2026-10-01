"""Filesystem / shell effects of inline interpreter code (z0int#55, action authority v1).

``python -c``, ``python - <<EOF``, ``node -e``, ``perl -e``, ``ruby -e`` hide writes, deletes and
secret reads from a shell parser. This module does a small, deterministic, *static* reading of that
code: it finds write/delete/secret-read operations (``open(p, "w")``, ``Path.write_text``,
``shutil.rmtree``, ``fs.writeFileSync``, ``os.system("...")`` ...) and evaluates their path argument
with a tiny evaluator that understands string literals, f-strings / template literals, ``Path(...)``
and ``/`` joins, ``os.path.join``, ``expanduser``, ``Path.home()``, ``os.homedir()``, ``os.environ``,
``sys.argv`` / ``process.argv``, and simple variable assignments.

It never executes anything. Imported lazily by ``action_effects`` only when a command carries code.

    analyze(code, lang, argv, env, home, cwd) -> {"ops": [(op, path|None)], "shell": [cmd, ...],
                                                  "mentioned": [path, ...], "secret_env": bool}
"""

from __future__ import annotations

import os
import re

_PY_STR = re.compile(r"""(?P<pre>[rRbBuUfF]{0,2})(?P<q>'''|\"\"\"|'|")""")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_XDG = {"XDG_CONFIG_HOME": "~/.config", "XDG_DATA_HOME": "~/.local/share", "XDG_STATE_HOME": "~/.local/state",
        "XDG_CACHE_HOME": "~/.cache"}
_SECRET_ENV = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|APIKEY|PRIVATE_KEY|CREDENTIAL|ACCESS_KEY)", re.I)

# (regex on the callee text right before "(", op, index of the path argument(s))
_PY_CALLS: tuple[tuple[re.Pattern[str], str, tuple[int, ...]], ...] = (
    (re.compile(r"(?:^|[^\w.])(?:shutil\.)?rmtree$"), "delete", (0,)),
    (re.compile(r"(?:^|[^\w.])os\.(?:remove|unlink|rmdir|removedirs)$"), "delete", (0,)),
    (re.compile(r"(?:^|[^\w.])os\.(?:makedirs|mkdir|chmod|chown|truncate|mkfifo)$"), "write", (0,)),
    (re.compile(r"(?:^|[^\w.])os\.(?:rename|replace|renames)$"), "move", (0, 1)),
    (re.compile(r"(?:^|[^\w.])os\.(?:symlink|link)$"), "write", (1,)),
    (re.compile(r"(?:^|[^\w.])shutil\.(?:copy|copy2|copyfile|copytree|copymode|copystat)$"), "write", (1,)),
    (re.compile(r"(?:^|[^\w.])shutil\.move$"), "move", (0, 1)),
    (re.compile(r"(?:^|[^\w.])(?:sqlite3\.connect|np\.save|np\.savez|np\.savetxt|torch\.save|joblib\.dump|pickle\.dump)$"),
     "write", (-1,)),
    (re.compile(r"\.(?:to_csv|to_json|to_parquet|to_pickle|to_excel|savefig|save_pretrained|save)$"), "write", (0,)),
    # node
    (re.compile(r"(?:^|[^\w])(?:fs\.|fsp\.|fs\.promises\.|promises\.)?(?:writeFileSync|appendFileSync|writeFile|appendFile|"
                r"createWriteStream|mkdirSync|mkdir|mkdtempSync|openSync|truncateSync|chmodSync|symlinkSync|outputFileSync|"
                r"writeJsonSync|outputFile|ensureDirSync)$"), "write", (0,)),
    (re.compile(r"(?:^|[^\w])(?:fs\.|fsp\.|fs\.promises\.|promises\.)?(?:rmSync|rmdirSync|unlinkSync|rm|rmdir|unlink|"
                r"removeSync|remove|emptyDirSync)$"), "delete", (0,)),
    (re.compile(r"(?:^|[^\w])(?:fs\.|fsp\.|fs\.promises\.)?(?:renameSync|rename|moveSync|move)$"), "move", (0, 1)),
    (re.compile(r"(?:^|[^\w])(?:fs\.|fsp\.|fs\.promises\.)?(?:copyFileSync|copyFile|cpSync|cp|copySync|copy)$"), "write", (1,)),
    # ruby
    (re.compile(r"(?:^|[^\w])File\.(?:write|binwrite)$"), "write", (0,)),
    (re.compile(r"(?:^|[^\w])FileUtils\.(?:rm_rf|rm_r|rm|rm_f|remove_dir|remove_entry)$"), "delete", (0,)),
    (re.compile(r"(?:^|[^\w])FileUtils\.(?:mkdir_p|mkdir|touch)$"), "write", (0,)),
    (re.compile(r"(?:^|[^\w])FileUtils\.(?:cp|cp_r|ln_s|install)$"), "write", (1,)),
    (re.compile(r"(?:^|[^\w])FileUtils\.mv$"), "move", (0, 1)),
    (re.compile(r"(?:^|[^\w])Dir\.mkdir$"), "write", (0,)),
)
_METHOD_OPS = {"write_text": "write", "write_bytes": "write", "touch": "write", "mkdir": "write", "symlink_to": "write",
               "hardlink_to": "write", "chmod": "write", "unlink": "delete", "rmdir": "delete", "rename": "move_self",
               "replace": "move_self"}
_SHELL_CALLS = re.compile(r"(?:^|[^\w.])(?:os\.system|os\.popen|subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|"
                          r"getstatusoutput)|execSync|exec|spawnSync|execFileSync|system|`)$")
_OPEN = re.compile(r"(?:^|[^\w.])(?:io\.|codecs\.|gzip\.|bz2\.|lzma\.)?open$")


# ----------------------------------------------------------------------------------------------- scanning
_QRX = {"'": re.compile(r"(?:[^'\\\n]|\\.)*"), '"': re.compile(r'(?:[^"\\\n]|\\.)*'), "`": re.compile(r"(?:[^`\\]|\\.)*", re.S)}


def _string_end(s: str, i: int, q: str) -> int:
    """index just after the closing quote of a string starting at s[i] (the opening quote)."""
    j = i + len(q)
    if len(q) == 3:
        k = s.find(q, j)
        return len(s) if k < 0 else k + 3
    k = _QRX[q].match(s, j).end()
    return k + 1 if k < len(s) and s[k] == q else k


def _match_close(s: str, i: int) -> int:
    """s[i] is an opening bracket; index of its closing bracket (strings skipped)."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [pairs[s[i]]]
    j = i + 1
    n = len(s)
    while j < n and stack:
        c = s[j]
        if c in "'\"`":
            q = s[j:j + 3] if s[j:j + 3] in ("'''", '"""') else c
            j = _string_end(s, j, q)
            continue
        if c == "#" and not stack[-1] == "}":
            pass
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return j
        j += 1
    return n


def _split_top(s: str, sep: str) -> list[str]:
    if sep not in s:
        return [s.strip()]
    if not any(c in s for c in "'\"`([{"):
        return [x.strip() for x in s.split(sep)] if sep != "/" else [x.strip() for x in re.split(r"(?<![/*])/(?![/=])", s)]
    out, depth, cur, j, n = [], 0, [], 0, len(s)
    while j < n:
        c = s[j]
        if c in "'\"`":
            q = s[j:j + 3] if s[j:j + 3] in ("'''", '"""') else c
            k = _string_end(s, j, q)
            cur.append(s[j:k])
            j = k
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if depth == 0 and s.startswith(sep, j) and not (sep == "/" and (s[j + 1:j + 2] in "/=" or s[j - 1:j] in "/*")):
            out.append("".join(cur))
            cur = []
            j += len(sep)
            continue
        cur.append(c)
        j += 1
    out.append("".join(cur))
    return [x.strip() for x in out]


class _Unknown(Exception):
    pass


class Evaluator:
    def __init__(self, code: str, lang: str, argv: list[str], env: dict, home: str, cwd: str):
        self.code, self.lang, self.argv, self.env, self.home, self.cwd = code, lang, argv, env, home, cwd
        self.vars: dict[str, str] = {}
        self._collect_vars()

    # -- variables: simple `name = expr` / `const name = expr` / `$name = expr` / `with open(..) as name`
    def _collect_vars(self) -> None:
        rx = re.compile(r"^[ \t]*(?:(?:const|let|var|my|our)\s+)?\$?([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[\w\[\]., ]+)?\s*=(?!=)\s*(.+?)\s*;?\s*$",
                        re.M)
        stmts = "\n".join(x for line in self.code.split("\n") for x in (_split_top(line, ";") if ";" in line else (line,)))
        for m in rx.finditer(stmts):
            self.vars[m.group(1)] = m.group(2)
        for m in re.finditer(r"for\s+([A-Za-z_]\w*)\s+in\s+(\[[^\]\n]*\]|\([^)\n]*\))", self.code):
            items = _split_top(m.group(2)[1:-1], ",")
            if items and items[0]:
                self.vars.setdefault(m.group(1), items[0])

    def ev(self, expr: str, depth: int = 0) -> str:
        if depth > 12:
            raise _Unknown
        e = expr.strip()
        while e.startswith("(") and _match_close(e, 0) == len(e) - 1:
            e = e[1:-1].strip()
        if not e:
            raise _Unknown
        # binary joins: a / b (pathlib), a + b (concat); separators inside strings are skipped
        parts = _split_top(e, "/") if "/" in e else [e]
        if len(parts) >= 2 and all(parts):
            return _join(*[self.ev(p, depth + 1) for p in parts])
        plus = _split_top(e, "+") if "+" in e else None
        if plus and len(plus) >= 2 and all(plus):
            return "".join(self.ev(p, depth + 1) for p in plus)
        # string literal
        m = _PY_STR.match(e)
        if m and (m.end() < len(e)) and e[0] not in "`":
            q = m.group("q")
            end = _string_end(e, m.end() - len(q), q)
            if end == len(e):
                body = e[m.end():end - len(q)]
                if q == '"' and self.lang in ("perl", "ruby"):
                    body = re.sub(r"#\{([^{}]+)\}", lambda mm: self.ev(mm.group(1), depth + 1), body)
                    body = re.sub(r"\$ENV\{\s*['\"]?(\w+)['\"]?\s*\}", lambda mm: self.ev("$ENV{%s}" % mm.group(1), depth + 1), body)
                    body = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda mm: self.ev("$" + mm.group(1), depth + 1), body)
                if "f" in m.group("pre").lower():
                    body = re.sub(r"\{([^{}]+)\}", lambda mm: self.ev(mm.group(1).split("!")[0].split(":")[0], depth + 1), body)
                if "r" not in m.group("pre").lower():
                    body = body.replace("\\\\", "\\")
                return body
        if e.startswith("`") and e.endswith("`") and len(e) > 1:
            return re.sub(r"\$\{([^{}]+)\}", lambda mm: self.ev(mm.group(1), depth + 1), e[1:-1])
        # perl-style "...$var..." strings are handled above; home / env / argv / cwd
        if re.fullmatch(r"(?:pathlib\.)?Path\.home\(\)|os\.homedir\(\)|require\(['\"]os['\"]\)\.homedir\(\)|Dir\.home|"
                        r"os\.path\.expanduser\(\s*['\"]~['\"]\s*\)|\$ENV\{\s*['\"]?HOME['\"]?\s*\}|ENV\[['\"]HOME['\"]\]|"
                        r"process\.env\.HOME|process\.env\[['\"]HOME['\"]\]", e):
            return self.home
        if re.fullmatch(r"os\.getcwd\(\)|process\.cwd\(\)|Dir\.pwd|(?:pathlib\.)?Path\.cwd\(\)|Path\(\)|__dirname", e):
            return self.cwd
        if re.fullmatch(r"tempfile\.(?:mkdtemp|gettempdir|mktemp)\([^)]*\)|os\.tmpdir\(\)|Dir\.tmpdir", e):
            return "/tmp/tmp.static"
        m = re.fullmatch(r"(?:os\.environ\[|os\.environ\.get\(|os\.getenv\(|process\.env\[|ENV\[|ENV\.fetch\()\s*['\"](\w+)['\"]\s*(?:,\s*(.+))?[\])]", e) or \
            re.fullmatch(r"process\.env\.(\w+)()", e) or re.fullmatch(r"\$ENV\{\s*['\"]?(\w+)['\"]?\s*\}()", e)
        if m:
            v = self._env(m.group(1))
            if v is None and m.group(2):
                return self.ev(m.group(2), depth + 1)
            if v is None:
                raise _Unknown
            return v
        m = re.fullmatch(r"(sys\.argv|process\.argv|ARGV|\$ARGV)\[(\d+)\]", e)
        if m:
            k = int(m.group(2)) - {"sys.argv": 1, "process.argv": 2, "ARGV": 0, "$ARGV": 0}[m.group(1)]
            if 0 <= k < len(self.argv):
                return self.argv[k]
            raise _Unknown
        # calls
        m = re.match(r"^([\w.]+)\s*\(", e)
        if m and _match_close(e, m.end() - 1) == len(e) - 1:
            fn, inner = m.group(1), e[m.end():-1]
            args = [a for a in _split_top(inner, ",") if a and "=" not in a.split("(")[0]] if inner.strip() else []
            if fn in ("Path", "pathlib.Path", "PurePath", "PosixPath", "os.path.join", "path.join", "path.resolve", "File.join",
                      "os.path.abspath", "os.path.realpath", "os.path.normpath", "str", "os.fspath", "String", "path.normalize",
                      "File.expand_path", "os.path.expanduser", "expanduser"):
                vals = [self.ev(a, depth + 1) for a in args] or [self.cwd]
                v = _join(*vals)
                if fn in ("os.path.expanduser", "expanduser", "File.expand_path") or v.startswith("~"):
                    v = self._tilde(v)
                if fn == "path.resolve" and not v.startswith("/"):
                    v = _join(self.cwd, v)
                return v
            if fn in ("os.path.dirname", "path.dirname", "File.dirname"):
                return os.path.dirname(self.ev(args[0], depth + 1))
            raise _Unknown
        # method chains on a receiver: X.expanduser() / X.resolve() / X.parent / X.joinpath(a, b)
        m = re.match(r"^(.*)\.(expanduser|resolve|absolute|parent|joinpath|with_suffix|with_name)(\(.*\))?$", e, re.S)
        if m and m.group(1):
            base = self.ev(m.group(1), depth + 1)
            meth, call = m.group(2), (m.group(3) or "")
            if meth == "expanduser":
                return self._tilde(base)
            if meth == "parent":
                return os.path.dirname(base.rstrip("/"))
            if meth == "joinpath":
                return _join(base, *[self.ev(a, depth + 1) for a in _split_top(call[1:-1], ",") if a])
            if meth == "with_name":
                return _join(os.path.dirname(base), self.ev(call[1:-1], depth + 1))
            if meth == "with_suffix":
                return os.path.splitext(base)[0] + self.ev(call[1:-1], depth + 1)
            return base
        if _IDENT.match(e) or re.fullmatch(r"\$[A-Za-z_]\w*", e):
            name = e.lstrip("$")
            if name in self.vars and self.vars[name].strip() != e:
                val = self.vars.pop(name)  # guard against self-reference
                try:
                    return self.ev(val, depth + 1)
                finally:
                    self.vars[name] = val
            if name in ("__file__",):
                raise _Unknown
        raise _Unknown

    def _env(self, name: str) -> str | None:
        if name == "HOME":
            return self.home
        if name in self.env and self.env[name] is not None:
            return self.env[name]
        if name in _XDG:
            return self._tilde(_XDG[name])
        if name in ("TMPDIR", "TMP", "TEMP"):
            return "/tmp"
        if name == "PWD":
            return self.cwd
        return None

    def _tilde(self, v: str) -> str:
        return self.home + v[1:] if v == "~" or v.startswith("~/") else v

    def try_str(self, expr: str) -> str | None:
        try:
            v = self.ev(expr)
        except (_Unknown, RecursionError, IndexError, ValueError):
            return None
        return v if isinstance(v, str) else None

    def try_ev(self, expr: str) -> str | None:
        try:
            v = self.ev(expr)
        except (_Unknown, RecursionError, IndexError, ValueError):
            return None
        if not isinstance(v, str) or not _pathlike(v):
            return None
        return self._tilde(v)


def _pathlike(v: str) -> bool:
    return 0 < len(v) < 300 and "\n" not in v and not re.search(r"[|`<>{}]|\s{2,}|^[#\-\s]", v)


def _join(*parts: str) -> str:
    out = ""
    for p in parts:
        p = str(p)
        if p.startswith("/") or p.startswith("~") or not out:
            out = p
        else:
            out = out.rstrip("/") + "/" + p
    return out


_INTERESTING = frozenset("""open read_text read_bytes write_text write_bytes touch mkdir symlink_to hardlink_to chmod unlink rmdir
rename replace system popen run call check_call check_output Popen getoutput getstatusoutput execSync exec spawnSync execFileSync
rmtree remove removedirs makedirs chown truncate mkfifo renames symlink link copy copy2 copyfile copytree copymode copystat move
connect save savez savetxt dump to_csv to_json to_parquet to_pickle to_excel savefig save_pretrained writeFileSync appendFileSync
writeFile appendFile createWriteStream mkdirSync mkdtempSync openSync truncateSync chmodSync symlinkSync outputFileSync
writeJsonSync outputFile ensureDirSync rmSync rmdirSync unlinkSync rm removeSync emptyDirSync renameSync moveSync copyFileSync
copyFile cpSync cp copySync write binwrite rm_rf rm_r rm_f remove_dir remove_entry mkdir_p ln_s install mv""".split())


def _calls(code: str):
    """(start, callee_text, args_text, dot_index) for every relevant call site; dot_index is the '.' before the final name."""
    for m in re.finditer(r"([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\(", code):
        raw = m.group(1)
        if raw.rsplit(".", 1)[-1].strip() not in _INTERESTING:
            continue
        start = m.end() - 1
        end = _match_close(code, start)
        callee = re.sub(r"\s+", "", raw)
        if "." in raw:
            dot = m.start() + raw.rfind(".")
        elif m.start() > 0 and code[m.start() - 1] == ".":
            dot = m.start() - 1
        else:
            dot = None
        yield m.start(), callee, code[start + 1:end], dot


def _receiver(code: str, dot: int | None) -> str | None:
    """The expression a method is called on: walk back over names, dots and balanced brackets."""
    if dot is None:
        return None
    j = dot
    pairs = {")": "(", "]": "["}
    while j > 0:
        c = code[j - 1]
        if c in pairs:
            depth, k = 0, j - 1
            while k >= 0:
                if code[k] == c:
                    depth += 1
                elif code[k] == pairs[c]:
                    depth -= 1
                    if depth == 0:
                        break
                elif code[k] in "'\"":
                    q = code[k]
                    k -= 1
                    while k >= 0 and code[k] != q:
                        k -= 1
                k -= 1
            j = max(k, 0)
            continue
        if c.isalnum() or c in "_.$":
            j -= 1
            continue
        break
    r = code[j:dot].strip()
    return r or None


_PATHISH = re.compile(r"""(?:(?:pathlib\.)?Path\.home\(\)(?:\s*/\s*(?:[rbf]?['"][^'"\n]*['"]))*|os\.path\.expanduser\(\s*[rbf]?['"][^'"\n]*['"]\s*\)|"""
                      r"""os\.homedir\(\)|['"`](?:~/|/home/|/etc/|/opt/|/usr/|/var/|/srv/|/root/|/Users/|/mnt/|/data/)[^'"`\n]*['"`])""")


def analyze(code: str, lang: str, argv: list[str], env: dict, home: str, cwd: str) -> dict:
    if lang == "node":  # require('os').homedir() -> os.homedir(); import fs from 'node:fs' needs nothing
        code = re.sub(r"require\(\s*['\"](?:node:)?(fs/promises|fs|os|path|child_process)['\"]\s*\)",
                      lambda m: {"fs/promises": "fsp", "child_process": "cp"}.get(m.group(1), m.group(1)), code)
    ev = Evaluator(code, lang, argv, env, home, cwd)
    ops: list[tuple[str, str | None]] = []
    shell: list[str] = []
    reads: list[str] = []
    for pos, callee, args_text, dot in _calls(code):
        base = callee.rsplit(".", 1)[-1]
        recv = _receiver(code, dot)
        args = _split_top(args_text, ",") if args_text.strip() else []
        pos_args = [a for a in args if not re.match(r"^[A-Za-z_]\w*\s*=(?!=)", a)]
        kw = {a.split("=", 1)[0].strip(): a.split("=", 1)[1] for a in args if re.match(r"^[A-Za-z_]\w*\s*=(?!=)", a)}
        # open(path, mode)
        if _OPEN.search(callee) or (lang == "perl" and base == "open"):
            if lang == "perl":
                if len(pos_args) >= 3:
                    mode = ev.try_str(pos_args[1]) or ""
                    target = pos_args[2]
                elif len(pos_args) == 2:
                    raw = ev.try_str(pos_args[1]) or ""
                    mm = re.match(r"^\s*(\+?[<>]{1,2}|\|)?\s*(.*)$", raw)
                    mode, target = (mm.group(1) or "<"), repr(mm.group(2))
                else:
                    continue
                path = ev.try_ev(target)
                (ops.append(("write", path)) if ">" in mode or "+" in mode else reads.append(path or ""))
                continue
            mode = kw.get("mode") or (pos_args[1] if len(pos_args) > 1 else "'r'")
            mval = ev.try_str(mode) or ""
            path = ev.try_ev(pos_args[0]) if pos_args else None
            if re.search(r"[wax+]", mval) or (lang == "ruby" and re.search(r"[wa+]", mval)):
                ops.append(("write", path))
            else:
                reads.append(path or "")
            continue
        if base == "open" and recv:  # Path(...).open("w")
            mode = ev.try_str(pos_args[0]) if pos_args else "r"
            path = ev.try_ev(recv)
            (ops.append(("write", path)) if mode and re.search(r"[wax+]", mode) else reads.append(path or ""))
            continue
        if base in ("read_text", "read_bytes") and recv:
            reads.append(ev.try_ev(recv) or "")
            continue
        if base in _METHOD_OPS and recv and lang == "python" and recv not in ("os", "shutil", "fs", "os.path"):
            path = ev.try_ev(recv)
            op = _METHOD_OPS[base]
            if op == "move_self" and path is None:
                continue  # str.replace(...) and friends: only a receiver we can resolve to a path is a rename
            if op == "move_self":
                ops.append(("delete", path))
                ops.append(("write", ev.try_ev(pos_args[0]) if pos_args else None))
            else:
                ops.append((op, path))
            continue
        if _SHELL_CALLS.search(callee) and pos_args:
            a0 = pos_args[0]
            if a0.startswith("[") and a0.endswith("]"):
                items = [ev.try_str(x) for x in _split_top(a0[1:-1], ",") if x]
                if items and all(isinstance(x, str) for x in items):
                    shell.append(" ".join(_shq(x) for x in items))  # type: ignore[arg-type]
                continue
            s = ev.try_str(a0)
            if s:
                shell.append(s)
            continue
        for rx, op, idx in _PY_CALLS:
            if rx.search(callee):
                if op == "move":
                    ops.append(("delete", ev.try_ev(pos_args[0]) if pos_args else None))
                    ops.append(("write", ev.try_ev(pos_args[1]) if len(pos_args) > 1 else None))
                else:
                    for i in idx:
                        a = pos_args[i] if (i < len(pos_args) and i >= 0) or (i == -1 and pos_args) else None
                        ops.append((op, ev.try_ev(a) if a else None))
                break
    # perl / shell-ish unlink / system in perl and ruby
    if lang in ("perl", "ruby"):
        for m in re.finditer(r"\bunlink\s+([^;]+);", code):
            ops.append(("delete", ev.try_ev(m.group(1).strip())))
        for m in re.finditer(r"\bsystem\s*\(?\s*(['\"])(.+?)\1", code):
            shell.append(m.group(2))
    mentioned = []
    for m in _PATHISH.finditer(code):
        v = ev.try_ev(m.group(0))
        if v:
            mentioned.append(v)
    secret_env = bool(re.search(r"(?:print|console\.log|puts|echo|say|sys\.stdout\.write|pprint)\s*\(?[^\n]*"
                                r"(?:os\.environ|getenv|process\.env|ENV)\b[^\n]*", code)) and bool(_SECRET_ENV.search(code)) \
        or bool(re.search(r"(?:print|console\.log|puts|pprint)\s*\(\s*(?:dict\()?\s*(?:os\.environ|process\.env|ENV)\s*\)?\s*\)", code))
    return {"ops": ops, "shell": shell, "reads": [r for r in reads if r], "mentioned": mentioned, "secret_env": secret_env}


def _shq(s: str) -> str:
    return s if re.fullmatch(r"[\w@%+=:,./~-]+", s) else "'" + s.replace("'", "'\\''") + "'"
