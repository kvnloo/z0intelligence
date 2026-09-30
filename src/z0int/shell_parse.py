"""A small, dependency-free POSIX-ish shell splitter for action-effect parsing (z0int#55).

It does not evaluate anything. It turns a command string into a flat list of *simple commands*
(argv + redirections + heredoc bodies), recursing into ``$( )``, backticks, ``( )`` subshells and
``{ }`` groups, and recording which connector joined each command to the previous one (so a
``curl ... | sh`` pipeline is visible). Words keep a flag telling whether any part was quoted or
contained an expansion (``$VAR``, ``$( )``), so callers can refuse to resolve unknown paths.
"""

from __future__ import annotations

OPS2 = ("&&", "||", "|&", ";;", ">>", "<<", "&>", ">&", "<&", "<>", ">|")
CONNECTORS = {";", "&&", "||", "|", "|&", "&", "\n", ";;"}
REDIRS = {">", ">>", "<", "<<", "<<-", "<<<", "&>", "&>>", ">&", "<&", "<>", ">|"}


class Word:
    __slots__ = ("text", "dynamic")

    def __init__(self, text: str, dynamic: bool = False):
        self.text = text
        self.dynamic = dynamic  # contains an unexpanded $VAR / $( ) / backtick / glob

    def __repr__(self) -> str:
        return f"Word({self.text!r}, {self.dynamic})"


class Simple:
    __slots__ = ("argv", "redirs", "heredocs", "joined_by", "nested")

    def __init__(self) -> None:
        self.argv: list[Word] = []
        self.redirs: list[tuple[str, Word]] = []
        self.heredocs: list[str] = []
        self.joined_by = ""  # connector before this command ("" for the first, "|" for a pipe stage, ...)
        self.nested = False  # came from a $( ) / backtick substitution

    @property
    def words(self) -> list[str]:
        return [w.text for w in self.argv]


class _Scanner:
    def __init__(self, s: str):
        self.s = s
        self.i = 0
        self.n = len(s)
        self.subs: list[str] = []  # command substitution bodies found anywhere

    def _match_paren(self, j: int) -> int:
        """s[j] is just after '$(' or '('; return index of the matching ')' (or n)."""
        depth, s, n = 1, self.s, self.n
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "'":
                k = s.find("'", j + 1)
                j = n if k < 0 else k + 1
                continue
            if c == '"':
                j = self._skip_dq(j + 1)
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        return n

    def _skip_dq(self, j: int) -> int:
        s, n = self.s, self.n
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                return j + 1
            if c == "$" and j + 1 < n and s[j + 1] == "(":
                k = self._match_paren(j + 2)
                j = k + 1
                continue
            j += 1
        return n

    def word(self) -> Word:
        s, n = self.s, self.n
        out: list[str] = []
        dyn = False
        while self.i < n:
            c = s[self.i]
            if c in " \t\n;&|()<>":
                break
            if c == "\\":
                if self.i + 1 < n and s[self.i + 1] == "\n":
                    self.i += 2
                    continue
                if self.i + 1 < n:
                    out.append(s[self.i + 1])
                self.i += 2
                continue
            if c == "'":
                k = s.find("'", self.i + 1)
                k = n if k < 0 else k
                out.append(s[self.i + 1:k])
                self.i = k + 1
                continue
            if c == '"':
                j = self.i + 1
                buf: list[str] = []
                while j < n and s[j] != '"':
                    if s[j] == "\\" and j + 1 < n:
                        buf.append(s[j + 1])
                        j += 2
                        continue
                    if s[j] == "$" and j + 1 < n and s[j + 1] == "(":
                        k = self._match_paren(j + 2)
                        self.subs.append(s[j + 2:k])
                        buf.append(s[j:k + 1])
                        dyn = True
                        j = k + 1
                        continue
                    if s[j] == "`":
                        k = s.find("`", j + 1)
                        k = n if k < 0 else k
                        self.subs.append(s[j + 1:k])
                        buf.append(s[j:k + 1])
                        dyn = True
                        j = k + 1
                        continue
                    if s[j] == "$":
                        dyn = True
                    buf.append(s[j])
                    j += 1
                out.append("".join(buf))
                self.i = j + 1
                continue
            if c == "$" and self.i + 1 < n and s[self.i + 1] == "(":
                k = self._match_paren(self.i + 2)
                self.subs.append(s[self.i + 2:k])
                out.append(s[self.i:k + 1])
                dyn = True
                self.i = k + 1
                continue
            if c == "`":
                k = s.find("`", self.i + 1)
                k = n if k < 0 else k
                self.subs.append(s[self.i + 1:k])
                out.append(s[self.i:k + 1])
                dyn = True
                self.i = k + 1
                continue
            if c in "$*?[{~":
                if c != "~" and c != "{":
                    dyn = True
            out.append(c)
            self.i += 1
        return Word("".join(out), dyn)


def split(command: str, *, _depth: int = 0) -> list[Simple]:
    """Flatten a command string into simple commands (argv, redirections, heredocs, connector)."""
    if _depth > 6 or not command:
        return []
    sc = _Scanner(command)
    s, n = command, len(command)
    cmds: list[Simple] = []
    cur = Simple()
    pending_heredocs: list[tuple[str, bool, Simple]] = []
    joined = ""
    extra: list[Simple] = []

    def flush(conn: str) -> None:
        nonlocal cur, joined
        if cur.argv or cur.redirs:
            cur.joined_by = joined
            cmds.append(cur)
        cur = Simple()
        joined = conn

    while sc.i < n:
        c = s[sc.i]
        if c in " \t":
            sc.i += 1
            continue
        if c == "\\" and sc.i + 1 < n and s[sc.i + 1] == "\n":
            sc.i += 2
            continue
        if c == "#" and (sc.i == 0 or s[sc.i - 1] in " \t\n;&|("):
            k = s.find("\n", sc.i)
            sc.i = n if k < 0 else k
            continue
        if c == "\n":
            sc.i += 1
            for delim, strip, owner in pending_heredocs:
                body: list[str] = []
                while sc.i < n:
                    k = s.find("\n", sc.i)
                    line = s[sc.i:] if k < 0 else s[sc.i:k]
                    sc.i = n if k < 0 else k + 1
                    if (line.lstrip("\t") if strip else line).strip() == delim:
                        break
                    body.append(line)
                owner.heredocs.append("\n".join(body))
            pending_heredocs = []
            flush("\n")
            continue
        if c in "()":
            # subshell / grouping: flatten (connectors around it still apply)
            flush(joined if not cur.argv else ";")
            sc.i += 1
            continue
        two = s[sc.i:sc.i + 2]
        three = s[sc.i:sc.i + 3]
        if three in ("<<-", "<<<", "&>>"):
            op = three
        elif two in OPS2:
            op = two
        elif c in ";&|<>":
            op = c
        else:
            op = ""
        if op:
            sc.i += len(op)
            if op in REDIRS or op == "<<":
                # fd-number prefix ("2>") was scanned as a word: drop it
                if cur.argv and cur.argv[-1].text.isdigit() and s[sc.i - len(op) - 1:sc.i - len(op)].isdigit():
                    cur.argv.pop()
                while sc.i < n and s[sc.i] in " \t":
                    sc.i += 1
                if op in (">&", "<&") and sc.i < n and (s[sc.i].isdigit() or s[sc.i] == "-"):
                    sc.word()
                    continue
                if sc.i < n and s[sc.i] == "&" and op in (">", ">>"):  # 2>&1 written as >&1
                    sc.i += 1
                    sc.word()
                    continue
                target = sc.word()
                if op in ("<<", "<<-"):
                    pending_heredocs.append((target.text.strip(), op == "<<-", cur))
                else:
                    cur.redirs.append((op, target))
                continue
            flush(op)
            continue
        w = sc.word()
        if w.text == "" and sc.i < n and s[sc.i] not in " \t\n;&|()<>":
            sc.i += 1  # defensive: never loop forever
            continue
        if not cur.argv and w.text in ("{", "}", "!", "then", "do", "else", "elif", "fi", "done", "esac", "in"):
            continue
        cur.argv.append(w)
    flush("")
    for sub in sc.subs:
        for inner in split(sub, _depth=_depth + 1):
            inner.nested = True
            extra.append(inner)
    return cmds + extra
