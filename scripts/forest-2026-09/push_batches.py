"""Commit and push files in ~3.5 MB batches (bigger pushes fail from this Mac)."""
import os, subprocess, sys
repo, listfile, label = sys.argv[1], sys.argv[2], sys.argv[3]
LIMIT = 3_500_000
files = [l.strip() for l in open(listfile) if l.strip()]
batches, cur, size = [], [], 0
for f in files:
    s = os.path.getsize(os.path.join(repo, f)) if os.path.exists(os.path.join(repo, f)) else 0
    if cur and size + s > LIMIT:
        batches.append(cur); cur, size = [], 0
    cur.append(f); size += s
if cur: batches.append(cur)
start = int(sys.argv[4]) if len(sys.argv) > 4 else 0
for i, b in enumerate(batches):
    if i < start: continue
    pf = f"/private/tmp/gwnf-basemap/_batch.txt"
    open(pf, "w").write("\n".join(b) + "\n")
    subprocess.run(["git", "-C", repo, "add", "--pathspec-from-file", pf], check=True)
    staged = subprocess.run(["git", "-C", repo, "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
    if not staged: continue
    msg = f"{label} (part {i+1} of {len(batches)})"
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", msg], check=True)
    for attempt in range(8):
        r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
        if r.returncode == 0: break
        print("push retry", i, r.stderr[-120:].strip(), flush=True); import time; time.sleep(5 + 5 * attempt)
    else:
        print("FAILED at batch", i, flush=True); sys.exit(1)
    print(f"pushed {i+1}/{len(batches)} ({len(b)} files)", flush=True)
print("ALL_PUSHED")
