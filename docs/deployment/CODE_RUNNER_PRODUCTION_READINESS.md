# Code Runner — Docker Architecture & Production Readiness

**Subject:** `courses/services/code_runner.py`
**Question answered:** How the Docker sandbox is architected, what the existing docs say about it, and whether it is safe to run in production.
**Verdict up front:** the runner is functionally complete and well-sandboxed *inside* the container, but its host-side execution model (**Docker-out-of-Docker**) is explicitly documented as **demo / single-tenant only**. It is **not** production-ready for a multi-tenant public platform without an isolation upgrade. Details below.

---

## 1. What the code actually does (Docker architecture)

### 1.1 Execution model — Docker-out-of-Docker (DooD)

`CodeRunner.run_submission()` connects to Docker via:

```python
client = docker.from_env()   # code_runner.py:761
client.ping()
```

`docker.from_env()` talks to the host Docker daemon over the shared **`/var/run/docker.sock`**. The Celery worker process (or its container) is a *client* of the host daemon — it does not run its own nested daemon. Every learner submission asks the host daemon to launch a sibling container.

**This is the core production risk.** Anyone who can reach that socket can control the host daemon (create privileged containers, mount the host FS, etc.). The module says so itself:

> WARNING (echoed from CLAUDE.md): this runner is Docker-out-of-Docker; the Docker daemon socket is shared with the host, so a sufficiently advanced attacker can escape to the host daemon. Demo / single-tenant use only. — `code_runner.py:15-17`

### 1.2 One container per submission

`run_submission()` runs the **whole evaluation suite in a single container** (`code_runner.py:780`), not one container per test. Startup (~300 ms) and C++/Java compiles happen once per submission. Flow:

1. Learner `code` + instructor `evaluation_script` → base64 → passed as env vars `CODE` / `EVAL` (+ `RUNNER`, and `TESTKIT` for C++) carrying the injected zero-dependency micro-harness.
2. Container command decodes env → files under `/tmp/work`, compiles if needed, runs the harness.
3. Harness runs every test, emits length-prefixed sentinel blocks on stdout (`<<<SCRIPT_RESULT ...>>>` / `<<<SCRIPT_END ...>>>`).
4. `_parse_script_output()` walks the stream → `list[ScriptTestResult]`.

Languages: `python`, `javascript`, `cpp`, `java`. Each has its own micro-harness (Python `unittest`, Node `assert` + `test()` registry, Java reflection over `test*`, C++ `TEST()` macro header). The runner images therefore need **only the base toolchain** — no jest/JUnit/gtest baked in.

### 1.3 Container security posture (`_container_security_kwargs`, `code_runner.py:845-866`)

Per-container hardening — this part is solid:

| Control | Value | Purpose |
|---|---|---|
| `runtime` | `settings.RUNNER_RUNTIME` (`runsc`/gVisor in prod, `runc` in dev) | User-space syscall interception |
| `network_disabled` | `True` | No network egress from learner code |
| `mem_limit` / `memswap_limit` | `128m` / `128m` | No swap escape; OOM cap |
| `nano_cpus` | `500_000_000` (0.5 CPU) | CPU throttle |
| `pids_limit` | `64` | Fork-bomb cap |
| `ulimits` | `fsize=10MB, nproc=64, nofile=128, cpu=10s` | File-size / proc / fd / CPU-time caps |
| `read_only` | `True` | Immutable root FS |
| `tmpfs` | `/tmp` = `size=32m,exec` | Only writable area; compiles happen here |
| `cap_drop` | `ALL` | No Linux capabilities |
| `security_opt` | `no-new-privileges:true` | No setuid privilege escalation |

Wall-clock budget: `container.wait(timeout=...)` + `container.kill()` on overshoot; `timeout_s = max(10, time_limit_ms // 1000 + 10)` (whole-suite budget + startup/compile headroom). Container always `remove(force=True)` in `finally`.

### 1.4 Error / failure taxonomy

- `DockerUnavailableError` — daemon unreachable / SDK missing. **Not** retried (operator action).
- `DockerTransientError` — connection/timeout `APIError`. Retried by `evaluate_coding_submission_task` (`autoretry_for`, `max_retries=3`).
- `ImageNotFound` → synthetic error result telling the operator to `docker pull` the image.
- Zero sentinels emitted (compile error, OOM, timeout, crash-before-first-test) → single synthetic `evaluation` error result carrying the stderr tail.

### 1.5 Configuration (`settings.py:311-316`)

```python
RUNNER_IMAGE_PYTHON     = env('RUNNER_IMAGE_PYTHON')
RUNNER_IMAGE_JAVASCRIPT = env('RUNNER_IMAGE_JAVASCRIPT')
RUNNER_IMAGE_CPP        = env('RUNNER_IMAGE_CPP')
RUNNER_IMAGE_JAVA       = env('RUNNER_IMAGE_JAVA')
RUNNER_RUNTIME = env('RUNNER_RUNTIME_DEV') if DEBUG else env('RUNNER_RUNTIME_PROD')
```

Defaults (README): `python:3.12-slim`, `node:20-alpine`, `gcc:14`, `eclipse-temurin:21-jdk-alpine`. `.env.example` sets `RUNNER_RUNTIME_DEV=runc`, `RUNNER_RUNTIME_PROD=runsc`. Runtime is picked by `DEBUG` — **prod defaults to gVisor automatically**.

---

## 2. What the docs describe about code-runner Docker usage

| Doc | Coverage | State |
|---|---|---|
| `docs/architecture/09-coding-exercises.md` | Authoritative reference. Sandbox constants, sentinel protocol, per-language harness contract, one-container-per-submission rationale, the DooD warning, the "learner can read the eval script" accepted limitation. | **Accurate — matches the code.** |
| `docs/deployment/AWS_DEPLOYMENT_ARCHITECTURE.md` | Runner as a required runtime process (#7); why it forces EC2 (needs real Docker daemon + `runsc`, no Fargate for that component); Graviton arm64 caveat (must build arm64 runner images); hardening checklist (§ risk table, line 368); dedicated-runner-instance recommendation. | **Accurate.** |
| `README.md` §"Coding runner" + env table | Runtime selection by `DEBUG`, image overrides, sandbox constant list. | **Accurate.** |
| `README.md` §6 "Coding-exercise execution" | Describes the runner. | **⚠ STALE — see below.** |
| `CLAUDE.md` | Origin of the DooD warning; describes script-eval model, tasks, guard layers. | Accurate. |

### 2.1 Documentation defect found

`README.md` §6 (lines ~335-355) still documents the **old I/O-pair grading model** that was removed:

- Claims learner code must define `solve(input_string)` and the harness loops over `INPUT_0..INPUT_{N-1}` env vars.
- Claims "visible vs hidden" test cases (`is_hidden`) with hidden tests run only on Submit.
- Calls the return type `SingleTestResult`.

None of that matches the current code. The real model is **script evaluation** (`CodingExercise.evaluation_script`), there are **no I/O pairs** (`CodingTestCase` removed in `0023_script_only_evaluation`), **no hidden/visible split** (Run and Submit both run the full suite), and the return type is `ScriptTestResult`. `09-coding-exercises.md` is the correct, current reference.

**Recommendation:** rewrite `README.md` §6 to point at `09-coding-exercises.md`, or delete the stale table. It actively misleads.

---

## 3. Can the existing system be used in production?

**Short answer: not as-is for a multi-tenant public platform.** It is fine for demo / single-tenant / trusted-cohort use — which is exactly the scope the docs claim.

### 3.1 What is production-grade already ✅

- **In-container isolation** is strong: gVisor runtime in prod, no network, dropped caps, read-only root, memory/CPU/PID/fd/file-size/CPU-time limits, no-new-privileges. A container escape has to beat gVisor first.
- **Resource exhaustion** is well-bounded (fork bombs, memory bombs, disk bombs, infinite loops all capped).
- **Reliability plumbing** exists: `acks_late` + idempotent task, transient-error retry, zombie reaper (`reap_stuck_coding_submissions_task`, 60 s beat), synthetic error results so the UI never hangs.
- **Deployment is thought through**: AWS doc mandates EC2 (not Fargate) for the worker, gVisor in `daemon.json`, arm64 runner images, dedicated-instance path.

### 3.2 Blocking issues for multi-tenant production ❌

1. **Docker-out-of-Docker / shared daemon socket (the big one).** gVisor sandboxes the *container*, but the worker still commands the host daemon. A container escape (or a compromise of the worker process itself) lands on a host with full daemon control. For untrusted, internet-facing, multi-tenant code execution this is the wrong isolation boundary. **Fix:** run the runner on a **dedicated, isolated instance** (blast-radius containment — already suggested in the AWS doc), and/or move to hardware-level isolation — **Kata Containers** or **Firecracker microVMs** — instead of DooD. AWS doc §hardening and README §6 both name this.

2. **No queue backpressure / concurrency cap on containers.** Each submission spawns a container with 0.5 CPU + 128 MB. Nothing limits how many run concurrently — that is bounded only by Celery worker concurrency. A submission spike (or a class of 300 learners hitting Submit at once) can exhaust host CPU/RAM. **Fix:** dedicated Celery queue for code execution with a capped worker pool sized to host capacity; consider a semaphore.

3. **Image pull / availability at scale.** `ImageNotFound` returns an error result but does not self-heal. Default images are Docker Hub (rate-limited for anonymous pulls). **Fix (already in AWS doc):** pre-pull / bake into the worker AMI, or push to ECR and pin `RUNNER_IMAGE_*` to ECR URIs.

4. **Startup latency + single-worker beat coupling.** The AWS doc runs the runner on the same worker instance as FFmpeg transcoding and beat ("self-healing singleton", min=max=1 ASG). One instance means no horizontal scale and code-exec latency competes with transcoding CPU. Acceptable at small scale; a growth ceiling.

### 3.3 Accepted limitations (by design, not blockers)

- Learner code and the evaluation script run in the same sandboxed process, so a learner can read `/tmp/work/evaluate.*` and extract the assertions. Documented as the same trade-off Udemy makes — gaming the tests only cheats the learner. Do not add in-container obfuscation.

---

## 4. Production go / no-go

| Scenario | Verdict |
|---|---|
| Demo, internal, single-tenant, or trusted-cohort (known learners) | ✅ **Ready** — keep gVisor, dedicated worker, pre-pulled images. |
| Multi-tenant, public sign-up, untrusted learners at scale | ❌ **Not ready** — must first: (1) replace DooD with a dedicated isolated runner host and/or Kata/Firecracker, (2) add a capped code-exec queue, (3) pin images to ECR. |

### 4.1 Minimum changes to reach public-multi-tenant readiness

1. **Isolate the runner host.** Dedicated instance, empty ingress SG, no S3 write, minimal IAM — so an escape lands on a near-empty box (AWS doc §hardening).
2. **Upgrade isolation boundary.** Move off shared-daemon DooD to Kata Containers or Firecracker (hardware-virtualized) for the untrusted workload.
3. **Cap concurrency.** Dedicated Celery queue + bounded worker pool sized to host CPU/RAM; reject/queue beyond the cap.
4. **Own your images.** Build the 4 runner images (arm64 if Graviton), push to ECR, pin `RUNNER_IMAGE_*`, pre-pull on the host.
5. **Fix stale docs.** Correct `README.md` §6 (still documents the removed I/O-pair model).
6. **Observability.** Alarm on container spawn rate, OOM-kills, timeout rate, reaper hits, daemon reachability.

---

## 5. Alternative: AWS Lambda as the code runner

The strongest fix for the DooD threat is to stop running Docker yourself. AWS Lambda executes each invocation inside a **Firecracker microVM** that AWS operates — hardware-level isolation, **no Docker daemon, no host to manage, no `/var/run/docker.sock` in your app at all.** The daemon-socket threat (§1.1, §3.2 #1) is removed *by construction*.

### 5.1 Why Lambda fits this workload especially well

Coding exercises are an **optional** course-section content item — most courses have zero, and even courses that have them see bursty, unpredictable "Run"/"Submit" traffic. That means code execution is **sparse + spiky + short-lived**:

- Long idle stretches platform-wide (many courses never invoke the runner).
- Occasional bursts (a learner mashing "Run" while coding; a cohort hitting "Submit").
- Every run is already `time_limit_ms`-capped and network-off.

Lambda **scales to zero** — you pay nothing while no code runs, and auto-scales for a burst. Compare with Kata/Firecracker-on-EC2, which needs a `.metal` instance billed 24/7 to serve a feature most courses don't use (paying for idle). For a sparse, optional feature, Lambda is both the better security story and the cheaper one.

> **Trade-off to accept:** low frequency means execution environments go cold, so the *first* run after an idle period pays a multi-second cold start (the C++/Java images are large). This is softened by the fact that Run/Submit are **already async** — the API returns `202 + task_id` and the client polls, so no user sits blocked on a request. Do **not** reach for provisioned concurrency here — it re-introduces the fixed idle cost Lambda exists to avoid, defeating the point for a sparse feature.

### 5.2 What changes in the code

`CodeRunner.run_submission()` stops calling `docker.from_env()` and instead invokes a Lambda via boto3. The per-language micro-harness **moves into the Lambda** (it runs there, not in the Celery worker):

```python
# conceptual — replaces the docker.containers.run(...) block in run_submission()
import boto3, json
resp = boto3.client('lambda').invoke(
    FunctionName=settings.RUNNER_LAMBDA[language],   # one fn per language
    Payload=json.dumps({'code': code_b64, 'eval': eval_b64}),
)
payload = json.loads(resp['Payload'].read())
# Lambda emits the same sentinel stream → reuse _parse_script_output(),
# OR return structured JSON and skip the parser. Either works.
```

- **Everything else stays.** The Celery tasks (`evaluate_coding_run_task`, `evaluate_coding_submission_task`), the `CodingSubmission` row lifecycle, `_parse_script_output`, the reaper, and the guard layers are unchanged — only the *execution backend* swaps.
- Map the existing error taxonomy onto Lambda: `DockerTransientError` → Lambda throttle/`TooManyRequestsException` (retryable via the existing `autoretry_for`); `DockerUnavailableError` → Lambda invoke/permission failure (not retried).
- Add a `RUNNER_BACKEND` setting (`docker` | `lambda`) so the existing Docker path stays for local dev and the Lambda path is prod-only — mirrors the current `RUNNER_RUNTIME` dev/prod split. Local dev keeps `runc`; no developer needs AWS to run tests.

### 5.3 Per-language Lambda build

Use **container-image Lambdas** (up to 10 GB image) so the language toolchain is bundled — same images you'd build for ECR anyway:

| Language | Base | Notes |
|---|---|---|
| python | `python:3.12` | harness runs directly |
| javascript | `node:20` | harness runs directly |
| cpp | image with `g++` | compile in `/tmp` at invoke (10 GB ephemeral) |
| java | image with `javac`/JDK | compile in `/tmp` at invoke |

One function per language (clean cold-start profile) or one function branching on `language`. Ceilings (10 GB `/tmp`, 10 GB RAM, 15 min timeout) are far above the current 128 MB / 32 MB / ~10 s needs.

### 5.4 Security — mandatory, Lambda-specific

Untrusted learner code now runs **inside your Lambda environment**, not a container you locked down. So re-establish the guarantees:

1. **Powerless execution role.** Learner code can read the role's credentials from env vars / the `169.254.169.254` metadata endpoint. The execution role must therefore have **zero permissions** beyond CloudWatch Logs — no S3, no DB, no other AWS access. Assume the creds are stolen; make them worthless.
2. **No network egress.** Replicate today's `network_disabled=True`. Put the function in a locked-down VPC subnet with **no NAT / no internet route**, or otherwise deny outbound. Learner code must not reach the internet or your VPC.
3. **Wipe `/tmp` every invocation — #1 Lambda footgun.** Lambda **reuses warm execution environments**, so a later invocation can read a previous learner's `/tmp/work/*`. The handler **must clear `/tmp/work` at the start (or end) of every invocation.** Missing this = cross-learner data leak. (The Docker path never had this problem — every container was fresh.)
4. **Re-impose process limits inside the handler.** Lambda gives the VM boundary but not Docker's `cap_drop`/`ulimits`. Run the learner code as a subprocess and apply `resource` rlimits (CPU time, memory, `fsize`, `nproc`) to mirror the current ulimit posture.
5. **Set function memory + timeout** = the `mem_limit` / `time_limit_ms` equivalents. The function timeout also hard-caps runaway loops.
6. **Cap concurrency with reserved concurrency.** Auto-scaling is great until a runaway class of learners becomes a runaway bill / downstream stampede. Set a max.

### 5.5 Lambda vs Kata (the two real upgrade paths)

| | Kata on `.metal` EC2 | Lambda |
|---|---|---|
| Isolation | VM you operate | Firecracker VM AWS operates |
| Daemon-socket threat | present but neutered | **gone entirely** |
| Host to manage | yes (`.metal` required for KVM) | none |
| Concurrency scaling | manual pool cap | automatic (cap via reserved) |
| Cost, sparse workload | metal billed 24/7 (pays for idle) | **$0 when idle**, pay-per-run |
| Code change | ~none (env flip) | **moderate** (rewrite backend + build fns) |
| Cold start | none | seconds (softened by async Run/Submit) |
| `/tmp` leak risk | none (fresh container) | **yes — must wipe each invocation** |

For this **optional, sparse** feature, Lambda wins on both security (no socket) and cost (scale-to-zero). Kata only wins if the runner were heavily and constantly used — which this feature is not.

### 5.6 Migration checklist

1. Build the 4 language container images as Lambda images; push to ECR.
2. Create 4 Lambda functions (or 1 branching) from those images; set memory + timeout per language.
3. Create a **zero-permission execution role** (CloudWatch Logs only).
4. Put functions in a **no-egress VPC subnet** (or deny outbound).
5. Set **reserved concurrency** ceilings.
6. Move each per-language harness into its Lambda handler; **add the `/tmp/work` wipe** at invocation start; apply `resource` rlimits to the learner subprocess.
7. Add `RUNNER_BACKEND` setting; implement the `lambda` branch in `CodeRunner.run_submission()` (boto3 `invoke`); keep the `docker` branch for local dev.
8. Map Lambda errors to `DockerTransientError` / `DockerUnavailableError` so the existing retry/reaper logic is untouched.
9. Smoke-test all 4 languages end-to-end (pass, fail, error, timeout, compile-crash) against the deployed functions.
10. Roll out prod-only; leave dev on the Docker/`runc` path.

---

## 6. References

- Code: `courses/services/code_runner.py`
- Tasks: `courses/tasks.py` (`evaluate_coding_run_task`, `evaluate_coding_submission_task`, `reap_stuck_coding_submissions_task`)
- Config: `career_college_backend/settings.py:311-316`, `.env.example:68-73`
- Authoritative feature doc: `docs/architecture/09-coding-exercises.md`
- Deployment: `docs/deployment/AWS_DEPLOYMENT_ARCHITECTURE.md` (runtime #7, EC2 rationale line 116, hardening line 368, build steps line 505)
- Stale (needs fixing): `README.md` §6
