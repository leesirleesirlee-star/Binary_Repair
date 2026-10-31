# `jobwrap routea` — required C merge (guest side)

The installed `jobwrap` is a root-owned C binary built in the guest. Its source
is not in this checkout, so the `routea` mode cannot be compiled here. This
file is the exact merge spec for VMer; `worker.py` is already implemented.

## CLI

```text
jobwrap routea <job-id>
```

Only that form is accepted. The browser, broker, and worker never pass a path,
a binary name, or an argv array. A bad argument exits 2 before any namespace or
uid change.

## Root-owned config (0600)

`/usr/local/etc/binrepair/routea.conf`, one tab-separated line per job:

```text
<job-id>  <job-dir>  <worker.py>  <work-dir>  <python>  <candidate-sha256>
```

Validation before `exec`:

- `job-id` matches `[A-Za-z0-9._-]{1,64}`;
- `job-dir` is under `/var/lib/binrepair/jobs/`;
- `worker.py` is the fixed `/opt/binrepair/workers/route_a/worker.py`;
- `work-dir` is under `/var/lib/binrepair/jobs/<job-id>/`;
- `python` is `/opt/angr-venv/bin/python`;
- candidate sha256 is 64 lowercase hex;
- the candidate at `<work-dir>/route_a/candidate.elf` exists and its
  `sha256sum` equals the configured value (exit 4 on mismatch).

## Execution order (same as the approved gate3 wrapper)

```text
unshare(CLONE_NEWNET | CLONE_NEWNS | CLONE_NEWPID)
  -> mount("proc", "/proc", "proc", 0, NULL)
  -> make / private (MS_REC|MS_PRIVATE)
  -> open root-owned evidence file
  -> setgroups(0, NULL)
  -> setgid(1003)
  -> setuid(1003)
  -> capset: all effective/permitted/inheritable = 0
  -> prctl(PR_SET_NO_NEW_PRIVS, 1)
  -> setrlimit: CPU 600s, AS 4 GiB, NOFILE 1024, FSIZE 256 MiB
  -> clearenv()
  -> whitelist env: PATH, HOME, TMPDIR, LANG, PYTHON,
                    GHIDRA_HOME, JAVA_HOME
  -> execv(python, [python, worker.py, "--job-dir", job_dir,
                    "--original", ..., "--contract", ...,
                    "--tests-dir", ..., "--out", ...,
                    "--candidate", ..., "--expected-candidate", hash])
```

No `system()`, no `popen()`, no shell, no inherited API keys or tokens.

## Evidence written by the wrapper

Root-owned, 0700 directory:

```text
/var/lib/binrepair/evidence/<UTC>-<job-id>/jobwrap-routea.json
```

Fields: `job_id`, `argv`, `uid`, `gid`, `cap_eff`, `no_new_privs`, `netns`,
`candidate_sha256`, `worker_sha256`, `config_sha256`, `exit_code`, `started_at`,
`finished_at`. A worker failure does not become `pass`; it is recorded and the
exit code is propagated.

## Acceptance tests for VMer

```text
jobwrap routea demo                 -> uid=1003, CapEff=0, NoNewPrivs=1, lo DOWN
sudo -u runner jobwrap routea demo  -> Permission denied, rc=1
jobwrap routea ../etc               -> bad argument, rc=2
jobwrap routea missing              -> unknown job, rc=3
candidate hash mismatch             -> rc=4
```

Only after these pass, point the broker at `jobwrap routea`; before that the
op is `not_run`.
