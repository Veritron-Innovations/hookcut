#!/usr/bin/env python3
"""
Create a zip archive of the repository including tracked files and untracked
files that are NOT ignored by .gitignore. Uses `git ls-files --exclude-standard`
so the .gitignore rules are respected.

Run from the repository root.
"""
import subprocess
import zipfile
import os
import sys

OUT = "hookcut_filtered.zip"

try:
    data = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]) 
except subprocess.CalledProcessError as e:
    print("git ls-files failed:", e, file=sys.stderr)
    sys.exit(2)

entries = [p for p in data.split(b"\0") if p]
print(f"Including {len(entries)} files in {OUT}")

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for b in entries:
        path = b.decode("utf-8")
        if not os.path.exists(path):
            # skip stale entries
            continue
        # Normalize to forward slashes inside archive
        arcname = path.replace('\\', '/')
        z.write(path, arcname=arcname)

print("Created:", OUT)
