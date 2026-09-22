"""Time-split ridge baseline for next-action family (critical path §16/§28)."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from .families import FAMILIES
from .compile import DEFAULT_OUT

PN = 96


def _hash(text: str, dim: int = PN) -> np.ndarray:
    x = np.zeros(dim, dtype=np.float64)
    t = (text or "").lower()
    if len(t) < 3:
        t = t.ljust(3)
    for i in range(len(t) - 2):
        h = hashlib.blake2b(t[i : i + 3].encode(), digest_size=8).digest()
        x[int.from_bytes(h, "little") % dim] += 1.0
    n = np.linalg.norm(x)
    if n:
        x /= n
    return x


def load_xy(path: Path) -> tuple[np.ndarray, np.ndarray]:
    idx = {n: i for i, n in enumerate(FAMILIES)}
    xs: list[np.ndarray] = []
    ys: list[int] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            rec = json.loads(line)
            fam = rec.get("family")
            if fam not in idx:
                continue
            prev = " ".join(rec.get("prev") or [])
            feat = np.concatenate([_hash(rec.get("user") or ""), _hash(prev)])
            xs.append(feat)
            ys.append(idx[fam])
    if not xs:
        raise SystemExit("no episodes")
    return np.stack(xs), np.asarray(ys, dtype=np.int64)


def ridge_fit(X: np.ndarray, y: np.ndarray, n_out: int, l2: float = 1e-2) -> np.ndarray:
    Y = np.eye(n_out, dtype=np.float64)[y]
    A = X.T @ X + l2 * np.eye(X.shape[1])
    B = X.T @ Y
    return np.linalg.solve(A, B)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="z0int-train-next-action")
    p.add_argument("--episodes", type=Path, default=DEFAULT_OUT / "next_action.jsonl")
    p.add_argument("--confirm-frac", type=float, default=0.2)
    p.add_argument("--mb", action="store_true", help="also train mushroom-body local_plasticity")
    p.add_argument("--n-kc", type=int, default=96)
    args = p.parse_args(argv)
    X, y = load_xy(args.episodes)
    n = len(y)
    n_c = max(100, int(n * args.confirm_frac))
    n_tr = n - n_c
    Xtr, ytr, Xcf, ycf = X[:n_tr], y[:n_tr], X[n_tr:], y[n_tr:]
    W = ridge_fit(Xtr, ytr, len(FAMILIES))
    pred = (Xcf @ W).argmax(axis=1)
    maj = Counter(ytr.tolist()).most_common(1)[0][0]
    report = {
        "n": n,
        "n_train": int(n_tr),
        "n_confirm": int(len(ycf)),
        "majority_confirm": float((ycf == maj).mean()),
        "ridge_confirm": float((pred == ycf).mean()),
        "ridge_n_params": int(W.size),
        "label_counts": {FAMILIES[i]: int((y == i).sum()) for i in range(len(FAMILIES))},
        "note": "Time-split; same features. MB uses Evolution Lab local_plasticity (KC→MBON).",
    }
    if args.mb:
        import sys
        from pathlib import Path as P

        from . import paths as _paths

        _lab = _paths.evolution_lab_root()
        if _lab is None:
            raise SystemExit(
                "--mb needs an Evolution Lab checkout; none found. "
                "Set EVOLUTION_LAB_ROOT, or run `python -m z0int onboard`."
            )
        sys.path.insert(0, str(_lab))
        from evolution_lab.jev_distill import fit_fly, predict_fly

        fly = fit_fly(Xtr, ytr, len(FAMILIES), n_kc=args.n_kc, seed=1)
        pred_f = predict_fly(Xcf, fly)
        report["mb_confirm"] = float((pred_f == ycf).mean())
        report["mb_n_params"] = int(fly["n_params"])
        report["mb_n_kc"] = int(args.n_kc)
    print(json.dumps(report, indent=2))
    dest = args.episodes.parent / "next_action_report.json"
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
