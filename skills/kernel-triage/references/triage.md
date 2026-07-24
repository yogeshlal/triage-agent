## Main chain of thought

Main chain-of-thought workflow that manages Linux kernel issue
triage on ARM/ARM64 systems by selecting and applying the right
``cots-*.md`` guidance available in this folder.

### Goal

This folder contains several ``cots-*.md`` files, each representing a
chain of thought for debugging, triaging, and presenting a solution for
one class of Linux kernel failure. This document is the entry point:
it defines log-intake rules, an log signature catalogue, the
mandatory applicability gate over the CoT files, and the reporting
handoff.

Focus is strictly on **mainline** Linux kernel behaviour on
**ARM / ARM64** targets. Vendor-parser artefacts (Qualcomm CrashScope,
TZ / TME / RPMh, `dmesg_TZ.txt`, JIRA URLs, Windows UNC paths) are out
of scope — a separate skill (`skills/devops/triage`) covers those.

### Get Logs

Logs may come from any of the following sources:

- **Log pasted on stdin** — raw text pasted directly into the
  conversation. Treat it as the primary log and begin analysis
  immediately without asking for a file. This is the most common
  public case (bug report body, IRC/Zulip paste, git send-email
  quote).

- **Local dmesg file** — a path to a captured `dmesg` blob on disk.
  Typical filenames: `dmesg.txt`, `dmesg.log`, `console.log`,
  `boot.log`, `serial.log`. Read in ~200-line chunks starting from the
  tail; on a crash the last panic frames are always at the end.
  Common capture commands the user may have run:
  `dmesg -H > dmesg.txt`, `journalctl -k --boot=-1 > prev.log`,
  `cat /sys/fs/pstore/dmesg-ramoops-0 > pstore.log`.

- **Extracted `pstore/ramoops` directory** — a directory (often copied
  from `/sys/fs/pstore/` after a reboot into a fresh kernel) containing
  one or more of:

    | pstore file                | Contents                                      |
    |----------------------------|-----------------------------------------------|
    | `dmesg-ramoops-<N>`        | Last kernel `printk` ring buffer before panic |
    | `dmesg-efi-<N>`            | Same, when the EFI variable backend is used   |
    | `console-ramoops-<N>`      | Console output (may include earlier printk)   |
    | `pmsg-ramoops-<N>`         | Userspace persistent messages                 |
    | `ftrace-ramoops-<N>`       | Per-CPU ftrace ring buffer at panic           |
    | `annotate-ramoops-<N>`     | User-annotated panic reason (if set)          |

  Higher `<N>` is older; `-0` is the most recent panic. Read
  `dmesg-ramoops-0` first (it holds the panic + call trace). Cross-
  check with `console-ramoops-0` for any earlier warnings the printk
  buffer overwrote. If `ftrace-ramoops-0` is present, use it to
  establish the pre-crash execution history.

- **Bug-report URL** — one of:
    - `https://lore.kernel.org/<list>/<msgid>/` — a mailing-list report.
      Fetch the raw message body; the attached log is usually inline
      after "Full dmesg attached below:" or in a `dmesg.txt` attachment.
    - `https://lkft.linaro.org/tests/…` — Linaro LKFT job page. Follow
      the "Attachments" section to the plain-text kernel log.
    - `https://linux.kernelci.org/test/…` or
      `https://staging.kernelci.org/…` — KernelCI test result. The
      "Logs" tab lists per-boot / per-test dmesg captures.

  In all three cases, download the log text and treat it as if it were
  a locally saved dmesg file — do not triage from the report summary
  alone; always analyse the actual log.

If none of the above are available, halt and ask the user for one of
these three inputs. Do not guess.

### Linux Kernel Log Signatures

Table of standard Linux kernel log signatures on ARM64, what they
mean, and which CoT file consumes them. Use this to orient at first
read of the log before running the applicability gate.

  | Signature (regex/substring)                                    | Meaning                                            | Primary CoT                                    |
  |----------------------------------------------------------------|----------------------------------------------------|------------------------------------------------|
  | `Linux version <ver> (<user>@<host>) (<toolchain>) #<n>`       | Build banner — carries version, toolchain, `-g<sha>` if built from a git tree | All (identify kernel first)                    |
  | `Kernel command line: `                                        | Kernel command line — check for `earlycon`, `console=`, `nokaslr`, `panic=`, `oops=`, KASAN args | `boot-probe-dt-failures` (boot); all           |
  | `Modules linked in: `                                          | Modules loaded at crash — first suspects for out-of-tree taint / recent probe | All                                            |
  | `Tainted: `                                                    | Kernel taint flags (`G`, `W`, `D`, `O`, `E`, `T`) — indicates prior WARN, module out-of-tree, unsigned | All                                            |
  | `CPU: <n> PID: <p> Comm: <c> ` … `Hardware name: `             | Faulting CPU / PID / task comm + board string (from DT `model` or SMBIOS) | All                                            |
  | `pc : ` / `lr : ` / `sp : `                                    | arm64 Program Counter / Link Register / Stack Pointer at fault | `panic-lockup-rcu-stall`                       |
  | `x0 : <hex> x1 : <hex> …`                                      | arm64 GPR dump — decode arg registers per AAPCS64 | `panic-lockup-rcu-stall`                       |
  | `Call trace:` … `<function>+0x<off>/0x<size>`                  | Kernel call trace with function+offset            | `panic-lockup-rcu-stall`                       |
  | `Unable to handle kernel <NULL pointer/paging request>`        | Bad kernel dereference — precedes oops            | `panic-lockup-rcu-stall`                       |
  | `Internal error: Oops:` / `Oops: <flags> [#<n>]`               | Kernel oops with fault class + repeat counter     | `panic-lockup-rcu-stall`                       |
  | `Kernel panic - not syncing:`                                  | Unrecoverable — often follows oops or explicit panic() | `panic-lockup-rcu-stall`                       |
  | `BUG: soft lockup - CPU#<n> stuck for <s>s! [<comm>:<pid>]`    | soft lockup detector — CPU never scheduled        | `panic-lockup-rcu-stall`                       |
  | `NMI watchdog: Watchdog detected hard LOCKUP on cpu <n>`       | hard lockup — IRQs disabled too long              | `panic-lockup-rcu-stall`                       |
  | `INFO: task <comm>:<pid> blocked for more than <n> seconds`    | hung_task detector — D-state task not scheduled   | `panic-lockup-rcu-stall`                       |
  | `rcu: INFO: rcu_(sched|preempt) self-detected stall on CPU`   | RCU stall — CPU in extended kernel context        | `panic-lockup-rcu-stall`                       |
  | `WARNING: possible circular locking dependency detected`       | lockdep ABBA / circular dependency                | `locking-memory-bugs`                          |
  | `WARNING: possible recursive locking detected`                 | lockdep recursive acquire                         | `locking-memory-bugs`                          |
  | `INFO: trying to register non-static key`                      | lockdep — non-init'd lock                         | `locking-memory-bugs`                          |
  | `BUG: KASAN: <use-after-free/slab-out-of-bounds/…> in <fn>`   | KASAN report — mem-safety bug                     | `locking-memory-bugs`                          |
  | `kmemleak: <N> new suspected memory leaks`                     | kmemleak — leaked kmalloc                         | `locking-memory-bugs`                          |
  | `Out of memory: Killed process <pid>`                          | OOM killer                                        | `locking-memory-bugs`                          |
  | `general protection fault, probably for non-canonical address` | GP fault (rare on arm64, but appears via GHES)    | `locking-memory-bugs`                          |
  | `SLUB: kmem_cache_<name>: <Redzone|Poison> overwritten`        | Slab corruption                                   | `locking-memory-bugs`                          |
  | `Bad device tree`                                              | libfdt / of_core parse failure                    | `boot-probe-dt-failures`                       |
  | `<driver>: probe of <dev> failed with error -<N>`              | Driver probe failure                              | `boot-probe-dt-failures`                       |
  | `deferred probe pending`                                       | Probe deferred > timeout — dep never showed up    | `boot-probe-dt-failures`                       |
  | `initcall <fn>+0x…/0x… returned <err> after <us> usecs`        | initcall_debug output — failing initcall          | `boot-probe-dt-failures`                       |
  | `irq <n>: nobody cared`                                        | Spurious IRQ w/ no handler consuming it           | `irq-gic-regression-bisect`                    |
  | `Disabling IRQ #<n>`                                           | Kernel gave up on a storming IRQ                  | `irq-gic-regression-bisect`                    |
  | `GICv3: <error>`                                               | arm64 GICv3 driver error                          | `irq-gic-regression-bisect`                    |
  | `BUG: workqueue lockup - pool <hex>/<n> stuck for <s>s`        | Workqueue lockup detector                         | `irq-gic-regression-bisect`                    |

### Guidance

- Read this file in full before taking any further actions.
- Identify the exact Linux kernel version from the `Linux version`
  banner. If a `-g<sha>` suffix is present (e.g.
  `6.9.0-rc4-g1234abcd`), that is the git commit the kernel was built
  from — check it out in the local kernel tree (if the user supplied
  `kernel_tree`) to correlate call-trace function+offset back to source.
- Read log files in chunks of ~200 lines from the tail. The panic
  frames are always at the end of `dmesg`. Adjust chunk size as needed
  for LLM ingestion.
- The **first strong signature** is what matters — not cascading
  symptoms. A kernel that panics inside a lockdep splat should be
  triaged as a lockdep bug (with the panic as downstream cleanup), not
  as a panic. Rewind the log until you find the earliest WARN / BUG /
  INFO line and start there.
- Cross-correlate: a `Modules linked in:` list on the panic line often
  names the buggy module; the `Tainted:` flags tell you if a prior
  WARN or out-of-tree driver has already poisoned the state.
- Never triage from the bug-report summary alone. Always analyse the
  attached / linked log.
- Produce a hypothesis, then validate it against at least two
  independent pieces of evidence in the log before locking it in.
- Follow the report template in `assets/report-template.md`
  strictly, including the final `lore.kernel.org`-ready snippet block.

### Tasks

#### Apply chain of thoughts in this directory

Each ``cots-*.md`` file in this directory represents an approach to
debug and analyse one class of Linux kernel failure. Each has a
section "Identify if Issue Applies". Use it to filter what does and
does not apply.

##### Step 1 — Mandatory Applicability Gate (MUST complete before any analysis)

This step is non-negotiable. Do NOT skip it or proceed to analysis
without it.

1. Enumerate every ``cots-*.md`` file in this directory.
2. For each file, read ONLY its section "Identify if Issue Applies".
3. Evaluate that section against the provided log.
4. Produce a pass/fail table in the report with columns:
   | CoT Doc | Applies? (Y/N) | Evidence (log source + exact string) |
5. For every doc marked N, write 1–2 sentences explaining why it was
   eliminated, citing the specific missing or present signature.
6. Proceed to Step 2 ONLY for docs marked Y.
7. If none apply, stop and report "No applicable chain of thought
   found", then list what additional logs or signatures are needed to
   classify (for example: request `dmesg` with `printk.time=1`,
   `earlycon` on the kernel command line, or `CONFIG_KASAN=y` build).

Any report that omits this applicability gate section is considered
incomplete.

##### Step 2 — Execute Applicable Chain(s) of Thought

Run the full analysis steps only for the ``cots-*.md`` docs marked Y
above. Do NOT mix branches across different docs; if two apply,
present each analysis separately and only merge conclusions in the
final root-cause section.

##### Step 3 — Produce Report and Solution

- Document all steps taken and the reasoning applied to reach a
  solution or possible root cause.
- **Report file naming** — always write the report to `/tmp/`:
  `/tmp/<crash-type>_<YYYYMMDD>_report.md`
  where `<crash-type>` is derived from the primary signature
  identified in Step 1 (e.g. `kasan_uaf`, `rcu_stall`, `probe_defer`,
  `lockdep_circular`, `soft_lockup`, `panic_null_deref`,
  `hung_task`, `oom_kill`, `slab_corruption`, `irq_storm`).
- Follow the template `assets/report-template.md` to present
  the report. The template ends with a `lore.kernel.org`-ready bug
  report snippet — fill in every field. This section is the
  distinguishing deliverable of the triage skill.
- Provide a fix as a unified diff if root cause is clear; otherwise
  provide a targeted debug patch (e.g. adding `pr_info` at suspected
  sites, enabling `dyndbg`, adding `WARN_ON_ONCE` at a hypothesised
  invariant break) to narrow the issue.
- Any report must include the applicability gate section highlighting
  the reasoning for why a particular chain of thought was applied,
  otherwise it is incomplete.
- If a good/bad SHA range is identifiable (user reports "worked on
  vX.Y, broken on vX.Z"), reference the `soft-bisect` skill at
  `skills/devops/soft-bisect/` for structured commit-range prediction
  before recommending a `git bisect run` reproducer.
- After writing the report, always end with a clear one-line message
  to the user stating the full report path, e.g.:
  `Report saved to: /tmp/kasan_uaf_20260707_report.md`
