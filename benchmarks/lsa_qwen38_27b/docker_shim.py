#!/usr/bin/env python3
"""Stand-in for `docker run` inside the Modal container that already IS the LSA serving image.

The LSA harness starts its SGLang server (and its kernel harness) with `run.runner=docker`, i.e.
`docker run --rm ... -v src:dst ... -e K=V ... <image> bash -lc '<inner>'`. On Modal we are already inside
`ghcr.io/prefrontal-ai/sglang:lsa-v0.1.0`, every mount the harness asks for is an identity mount
(`path:path`), so the faithful equivalent is to run `<inner>` here with the requested environment.
Anything else (a non-identity mount, a subcommand other than `run`) is refused rather than guessed.
"""
import os
import sys

VALUE_FLAGS = {"-v", "--volume", "-e", "--env", "--network", "--gpus", "--name", "--shm-size", "--ipc",
               "-w", "--workdir", "--entrypoint", "--user", "-u"}


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "run":
        print(f"docker shim: only `docker run` is supported, got {argv[:1]}", file=sys.stderr)
        return 2
    args, env, i = argv[1:], {}, 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            break
        key, _, inline = a.partition("=")
        if key in VALUE_FLAGS:
            value = inline if inline else args[i + 1]
            i += 1 if inline else 2
            if key in ("-v", "--volume"):
                src, dst = value.split(":")[:2]
                if os.path.realpath(src) != os.path.realpath(dst):
                    print(f"docker shim: refusing non-identity mount {value}", file=sys.stderr)
                    return 2
            elif key in ("-e", "--env"):
                k, _, v = value.partition("=")
                env[k] = v if _ else os.environ.get(k, "")
            continue
        i += 1  # boolean flags: --rm, -it, ...
    command = args[i + 1:]  # drop the image name
    if not command:
        print("docker shim: no command after the image", file=sys.stderr)
        return 2
    os.environ.update(env)
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
