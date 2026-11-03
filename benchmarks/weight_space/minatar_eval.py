"""Score a 600-32-32-6 tanh policy on official MinAtar 1.0.15, as Experiment 5 scored it.

Mean score per episode; actions sampled from softmax(logits); sticky actions 0.1 (MinAtar's own);
episodes cut at 10,000 steps. Observation: the 10x10 grid in 6 channels (player, danger, reward,
projectile, trail, gauge), input i = (row*10+col)*6+channel, built from the game's own channels
by the mapping the prompt states ("channel k (name) <- src, src; ...").

Usage: python minatar_eval.py NET.npz --game breakout --prompt PROMPT.txt --episodes 200 --seed 0
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import numpy as np
from minatar import Environment

_MAP = re.compile(r"channel (\d) \((\w+)\) <- ([^;.]+)")


def channel_map(prompt: str) -> dict[int, list[str]]:
    line = next(ln for ln in prompt.splitlines() if ln.startswith("How the 6 channels are built"))
    out = {}
    for k, _name, srcs in _MAP.findall(line):
        out[int(k)] = [] if srcs.strip() == "nothing" else [s.strip() for s in srcs.split(",")]
    assert sorted(out) == list(range(6)), out
    return out


def obs_index(env: Environment, cmap: dict[int, list[str]]):
    chans = env.env.channels
    return [(k, [chans[s] for s in srcs]) for k, srcs in cmap.items()]


def observe(env: Environment, idx) -> np.ndarray:
    st = env.state()  # 10 x 10 x C bool
    x = np.zeros((10, 10, 6), dtype=np.float32)
    for k, srcs in idx:
        if srcs:
            x[:, :, k] = st[:, :, srcs].any(axis=2)
    return x.reshape(-1)


def policy(net):
    W1, b1 = net["actor/dense1/kernel"], net["actor/dense1/bias"]
    W2, b2 = net["actor/dense2/kernel"], net["actor/dense2/bias"]
    W3, b3 = net["actor/logits/kernel"], net["actor/logits/bias"]

    def logits(X):
        h1 = np.tanh(X @ W1 + b1)
        h2 = np.tanh(h1 @ W2 + b2)
        return h2 @ W3 + b3
    return logits


def evaluate(net, game: str, cmap, episodes: int, seed: int, max_steps: int = 10_000, batch: int = 64) -> dict:
    rng = np.random.default_rng(seed)
    f = policy(net)
    scores, lengths = [], []
    started = 0
    while started < episodes:
        n = min(batch, episodes - started)
        envs = []
        for j in range(n):
            e = Environment(game, sticky_action_prob=0.1)
            e.seed(seed * 100_003 + started + j)
            e.reset()
            envs.append(e)
        started += n
        idx = obs_index(envs[0], cmap)
        ret = np.zeros(n)
        steps = np.zeros(n, dtype=int)
        alive = np.ones(n, dtype=bool)
        while alive.any():
            live = np.flatnonzero(alive)
            X = np.stack([observe(envs[i], idx) for i in live])
            L = f(X).astype(np.float64)
            L -= L.max(axis=1, keepdims=True)
            P = np.exp(L)
            P /= P.sum(axis=1, keepdims=True)
            u = rng.random(len(live))[:, None]
            acts = (P.cumsum(axis=1) < u).sum(axis=1).clip(0, 5)
            for i, a in zip(live, acts):
                r, term = envs[i].act(int(a))
                ret[i] += r
                steps[i] += 1
                if term or steps[i] >= max_steps:
                    alive[i] = False
        scores += ret.tolist()
        lengths += steps.tolist()
    s = np.asarray(scores)
    return {"mean": float(s.mean()), "se": float(s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 1 else 0.0,
            "episodes": len(s), "max": float(s.max()), "mean_length": float(np.mean(lengths)),
            "seed": seed, "max_steps": max_steps}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("net")
    ap.add_argument("--game", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--episodes", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    net = dict(np.load(a.net))
    from pathlib import Path
    res = evaluate(net, a.game, channel_map(Path(a.prompt).read_text()), a.episodes, a.seed)
    json.dump(res, sys.stdout)
    print()


if __name__ == "__main__":
    main()
