#!/usr/bin/env python3
"""Freeze the existing approved interpreter/dependencies without installing packages."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venv", type=Path)
    parser.add_argument("--base", type=Path, default=Path.home()/".local/lib/z0int-runtime")
    args = parser.parse_args()
    venv = args.venv.resolve()
    python = (venv/"bin/python").resolve()
    inventory = subprocess.check_output([str(venv/"bin/python"), "-c", "import importlib.metadata,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())))"], text=True)
    # Inventory plus interpreter bytes name the original snapshot. The manifest records
    # all source origins; writable/incomplete snapshots are never accepted as releases.
    identity = hashlib.sha256(inventory.encode()+python.read_bytes()).hexdigest()
    target = args.base/identity
    if target.exists():
        if not (target/"RUNTIME.json").is_file():
            raise SystemExit("Incomplete runtime exists: "+str(target))
        print(target)
        return
    args.base.mkdir(parents=True, exist_ok=True)
    temporary = args.base/(".stage-"+identity+"-"+str(os.getpid()))
    temporary.mkdir()
    subprocess.run(["cp", "-a", "--reflink=auto", str(venv), str(temporary/".venv")], check=True)
    subprocess.run(["cp", "-a", "--reflink=auto", str(python.parent.parent), str(temporary/"python")], check=True)
    sites = list((temporary/".venv/lib").glob("python*/site-packages"))
    if len(sites) != 1:
        raise SystemExit("Expected exactly one site-packages directory")
    site = sites[0]
    editable_origins = {}
    for pointer in site.glob("__editable__*.pth"):
        editable_origins[pointer.name] = pointer.read_text()
        pointer.write_text("# Editable checkout disabled in immutable runtime; release supplies PYTHONPATH.\n")
    # The learner remains a separately pinned subprocess in loop.json. Freeze its
    # installed library dependency too, rather than following the editable checkout.
    metadata = subprocess.check_output([str(venv/"bin/python"), "-c", "import evolution_lab,json; print(json.dumps(list(evolution_lab.__path__)))"], text=True)
    evolution = Path(json.loads(metadata)[0])
    subprocess.run(["cp", "-a", "--reflink=auto", str(evolution), str(site/"evolution_lab")], check=True)
    for name in ("python", "python3", "python3.13"):
        link = temporary/".venv/bin"/name
        if link.is_symlink():
            link.unlink()
        link.symlink_to("../../python/bin/"+python.name)
    (temporary/".venv/pyvenv.cfg").write_text("home = "+str(target/"python/bin")+"\ninclude-system-site-packages = false\n")
    receipt = {"identity": identity, "created_at": time.time(), "source_venv": str(venv), "source_python": str(python), "distributions": json.loads(inventory), "disabled_editable_pointers": editable_origins, "frozen_evolution_library": str(evolution), "source_scope": "Dependency snapshot only; z0int source comes exclusively from the selected release PYTHONPATH"}
    (temporary/"RUNTIME.json").write_text(json.dumps(receipt, indent=2)+"\n")
    subprocess.run(["chmod", "-R", "a-w", str(temporary)], check=True)
    temporary.rename(target)
    print(target)


if __name__ == "__main__":
    main()
