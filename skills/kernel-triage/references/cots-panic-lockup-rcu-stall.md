# cots-panic-lockup-rcu-stall.md

## Issue Class

Linux kernel forward-progress failures on ARM64: kernel panic,
oops, `BUG:`/`BUG_ON`, `Unable to handle kernel <NULL pointer / paging
request>`, soft lockup, hard lockup (NMI watchdog), hung task
(`hung_task_timeout_secs`), and RCU CPU stall (`rcu_sched` /
`rcu_preempt`). These share a common signature template — an
architecture register dump plus a `Call trace:` — but differ in the
lead-in `BUG:` / `INFO:` line and in what "the CPU was doing" at the
time of detection.

---

## 1 — Acquire Logs

Collect all of the following before analysis. Partial data leads to
false conclusions (particularly: mistaking the panic that follows an
oops for the primary bug).

- Full boot + runtime kernel log:
  - Pasted `dmesg`, saved file, or `pstore/dmesg-ramoops-0` (see
    `triage.md` for source rules).
  - Consider both the `dmesg-ramoops-0` (kernel ring buffer at panic)
    AND `console-ramoops-0` (earlier console output). Warnings that
    happened long before the panic may only be in `console-ramoops-0`
    if the ring buffer wrapped.
- Kernel build banner: line starting with `Linux version <ver> (…)`.
  Capture the version, the toolchain string in parentheses, and any
  `-g<sha>` suffix. Note KASAN / lockdep / debug options if visible in
  the toolchain / config output.
- Kernel command line: `Kernel command line: <args>`. Check for
  `panic=`, `oops=panic`, `nmi_watchdog=`, `rcu_cpu_stall_timeout=`,
  `hung_task_timeout_secs=`, `nokaslr`, `crashkernel=`,
  `console=`, `earlycon`.
- `Modules linked in:` (usually on the line immediately before the
  register dump). Note out-of-tree taint markers `[<name>](O)` etc.
- `Tainted:` flags (`G`, `W`, `D`, `O`, `E`, `T`, `L`, `K`). `W` means
  a prior `WARN_ON` — that WARN may be the real bug, not the panic.
- Faulting-context header: `CPU: <n> PID: <p> Comm: <c>` plus
  `Hardware name: <SoC/board string>`.
- arm64 register dump: `pc`, `lr`, `sp`, `x0..x30`, sometimes `ESR`
  (Exception Syndrome Register) and `FAR` (Fault Address Register) on
  synchronous exceptions.
- `Call trace:` — one entry per frame, `<symbol>+0x<offset>/0x<size>`.
- For soft/hard lockup: the "which task is stuck" line + a
  cross-check of other CPUs (`Sending NMI from CPU <x> to CPUs …`).
- For RCU stall: `rcu: INFO: rcu_<flavour> self-detected stall on CPU`
  or `rcu: INFO: rcu_<flavour> detected stalls on CPUs/tasks: {<mask>}`;
  the stack of the stalled CPU is dumped next.
- For hung task: `INFO: task <comm>:<pid> blocked for more than <n>
  seconds`; the D-state task's stack follows.

---

## 2 — Identify if Issue Applies

- Action: Locate the **first strong signature** in the log (earliest in
  time, not the last one before the log ends). Use the classification
  table below to pick **one primary branch**.

- Normal Behaviour: kernel makes forward progress — no `BUG:` /
  `WARNING:` / `Oops:` / `panic:` / `stall` / `lockup` / `hung task`
  markers; on any panic path, expect a full register dump + call
  trace to be present.

- Abnormal Behaviour: any of the signatures in the table below.

- Next Step:
  - Proceed with analysis only if a signature in the table matches.
  - If the log ends mid-line without a panic marker, treat it as a
    **complete freeze** and hand off to Possibility 5 below.
  - If no register dump / call trace is present after an oops line,
    request a rebuild with `CONFIG_DEBUG_INFO=y` and a re-capture.

### Classification Signatures (pick primary)

| Category                       | Positive signature (exact/near-exact)                                                                       | Primary evidence                                     |
|--------------------------------|-------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| Kernel oops / panic            | `Unable to handle kernel …`, `Internal error: Oops:`, `Kernel panic - not syncing:`, `BUG:` (not `BUG: soft lockup`) | Register dump + call trace immediately after         |
| Soft lockup                    | `watchdog: BUG: soft lockup - CPU#<n> stuck for <s>s! [<comm>:<pid>]`                                       | Stack of the stuck CPU + optionally IPI'd CPUs       |
| Hard lockup                    | `watchdog: Watchdog detected hard LOCKUP on cpu <n>` OR `NMI watchdog:`                                     | Register dump from NMI context                       |
| Hung task                      | `INFO: task <comm>:<pid> blocked for more than <n> seconds`                                                 | `Not tainted` / `Tainted:` line + D-state task stack |
| RCU stall                      | `rcu: INFO: rcu_<flavour> (self-detected )?stall on CPU`                                                    | Stalled CPU stack + `Sending IPI to non-responding CPUs` |
| Complete freeze (no marker)    | Log ends mid-line; no `BUG:` / `panic:`; no reset                                                           | Absence of a normal shutdown / reboot line           |

If multiple markers exist, pick the **earliest** — later ones are
usually secondary effects (an oops during a WARN handler; a panic
during an OOM kill; a soft lockup because RCU held everything up).

---

## 3 — Take Steps to Analyse the Issue

Iterate over the following possibilities to establish the best viable
root cause and present a solution:

### Possibility 1: Kernel oops / panic (bad dereference or explicit BUG)

- Step 1 — Issue Pattern:
  - Line reading `Unable to handle kernel NULL pointer dereference at
    virtual address 0000000000000<off>` or
    `Unable to handle kernel paging request at virtual address
    <hex>`, followed by an arm64 register dump and a `Call trace:`.

- Step 2 — Understand Pattern:
  - Decode the fault address:
    - Address in the low 4 KiB (`0x0..0xfff`) → NULL deref with a
      structure-field offset — the offset in the address is the
      offending struct field.
    - Address in `0xffff_8000_...` range → kernel linear map; likely
      valid kernel address (use-after-free or wrong pointer).
    - Address in userspace range on a kernel PC → SMEP-style bug or
      pt_regs corruption.
  - Decode `ESR_EL1` if present: bits[31:26] = Exception Class (`0x25`
    = Data Abort from EL1, `0x21` = Instruction Abort from EL1),
    `IFSC/DFSC` field tells you translation-fault vs
    permission-fault.
  - Decode `pc` to the function+offset from the Call trace's top frame.

- Step 3 — Correlated Pattern:
  - Match against known regressions: e.g. any patch that
    adds a new field to a hot struct, or any recent driver that adds
    an unchecked pointer path in probe/remove.
  - Check `Modules linked in:` — if a single out-of-tree module is
    listed with `(O)`, it is the first suspect.
  - Check `Tainted:` for `W` — a prior WARN in the same subsystem
    often precedes the panic in the log.

- Step 4 — Form Hypothesis:
  - Formulate: "function `<A>` at offset `+0x<off>` dereferenced
    `<struct>-><field>` which was NULL because `<init path>` didn't
    run, OR because it was freed by `<free path>`."
  - Cross-check by opening the source at the SHA from the `Linux
    version` banner (if `kernel_tree` provided). If the offset
    matches a specific field access, print the struct definition and
    the callers of `<A>`.

- Step 5 — Validate Hypothesis:
  - Look for a matching allocation / init / free call earlier in the
    log for the same task.
  - Look for other CPUs' backtraces (if multi-CPU dump was emitted)
    running the same function — concurrency bug indicator.
  - If confirmed, propose either a NULL check with a clear error
    return, or an ordering fix (e.g. probe-order or refcount fix).

### Possibility 2: Soft lockup — CPU stuck in kernel context

- Step 1 — Issue Pattern:
  - `watchdog: BUG: soft lockup - CPU#<n> stuck for <s>s! [<comm>:<pid>]`
    (typically 22s at default threshold), followed by a register
    dump + call trace for CPU `<n>`.

- Step 2 — Understand Pattern:
  - Soft lockup = the task on that CPU has not called `schedule()`
    for `<s>` seconds, but IRQs and NMIs are still running (that's
    how the detector fires).
  - Look at the top-of-stack function on the stuck CPU. Common
    patterns: (a) infinite loop in a driver worker, (b) polling
    without cond_resched(), (c) livelock on a spinlock re-acquire.

- Step 3 — Correlated Pattern:
  - Cross-check other CPUs' backtraces if present — a hard lockup on
    another CPU can starve this one.
  - Ftrace ring buffer (`ftrace-ramoops-0`) usually shows what
    ran on this CPU just before the stall started.

- Step 4 — Form Hypothesis:
  - "Function `<X>` loops on `<condition>` without releasing the CPU
    when `<condition>` remains false for `<reason>`."

- Step 5 — Validate Hypothesis:
  - Look for a recent commit touching `<X>` that removed a
    `cond_resched()` or `schedule()` call.
  - Suggest a debug patch adding `WARN_ONCE()` inside the loop with
    a bounded iteration counter to confirm.

### Possibility 3: Hard lockup — CPU with IRQs disabled

- Step 1 — Issue Pattern:
  - `watchdog: Watchdog detected hard LOCKUP on cpu <n>` from the
    perf/NMI watchdog; the register dump is from NMI context.

- Step 2 — Understand Pattern:
  - Hard lockup = IRQs stayed disabled for the full watchdog
    threshold (typically 10s). Root causes on arm64: infinite loop
    inside `local_irq_disable()` region, deadlock on a raw_spinlock,
    endless IPI processing, faulty pseudo-NMI setup.
  - Check `pc` and `lr` at NMI capture: usually points at the
    spinning function.

- Step 3 — Correlated Pattern:
  - Look for raw_spin_lock callers on the same lock from other CPUs.
  - Check for CPU-hotplug in progress, PSCI CPU_OFF, or per-CPU
    resource shutdown during the lockup window.

- Step 4 — Form Hypothesis:
  - "raw_spin_lock on `<lock>` never released because CPU `<m>`
    holding it was in `<state>`."

- Step 5 — Validate Hypothesis:
  - Enable `CONFIG_DEBUG_LOCK_ALLOC` and `CONFIG_PROVE_LOCKING`;
    reproduce and read the lockdep chain-holder report.

### Possibility 4: Hung task — D-state longer than threshold

- Step 1 — Issue Pattern:
  - `INFO: task <comm>:<pid> blocked for more than <n> seconds`;
    task stack shows `__schedule` → `schedule` → `schedule_timeout` /
    `io_schedule` / `rwsem_down_*` / `mutex_lock_*`.

- Step 2 — Understand Pattern:
  - The task is TASK_UNINTERRUPTIBLE waiting on something. Look at
    the stack to see WHAT: mutex, rwsem, completion, bio.
  - Hung task is a **symptom**, not a bug — find the holder /
    completer that isn't making progress.

- Step 3 — Correlated Pattern:
  - Search the log for other stacks holding the same
    mutex/rwsem/completion (echo /proc/sysrq-trigger w output if
    `sysrq_show_all_blocking_workers` was enabled).
  - If waiting on I/O — look for storage / network driver errors
    earlier in the log.

- Step 4 — Form Hypothesis:
  - "Task waits on `<sync obj>`; holder is `<comm2>:<pid2>` in state
    `<state>` waiting on `<other obj>` — circular dependency."

- Step 5 — Validate Hypothesis:
  - Cross-reference with lockdep report if present.
  - Suggest `echo w > /proc/sysrq-trigger` capture and rerun.

### Possibility 5: RCU CPU stall

- Step 1 — Issue Pattern:
  - `rcu: INFO: rcu_<flavour> self-detected stall on CPU` OR
    `rcu: INFO: rcu_<flavour> detected stalls on CPUs/tasks: { <n> }`.

- Step 2 — Understand Pattern:
  - Self-detected: CPU `<n>` didn't pass through a quiescent state
    (didn't schedule / didn't leave RCU-critical section) within the
    stall timeout (default 21s, `rcu_cpu_stall_timeout=`).
  - "Detected" (non-self): some OTHER CPU noticed `<n>` was stuck.
    The stuck CPU stack tells you what it was doing.

- Step 3 — Correlated Pattern:
  - `rcu_preempt` flavour → CONFIG_PREEMPT_RCU=y kernel; commonly
    the stall is a preemption-disabled loop.
  - `rcu_sched` → CONFIG_PREEMPT_NONE / VOLUNTARY; commonly a long
    kernel path without cond_resched.
  - `rcu_bh` → deprecated but appears on old stable kernels.

- Step 4 — Form Hypothesis:
  - "CPU `<n>` in `<function>` never reached a quiescent state
    because it was inside `preempt_disable()` / `rcu_read_lock()`
    for `<reason>`."

- Step 5 — Validate Hypothesis:
  - `rcutorture` or reproducer; enable `CONFIG_RCU_CPU_STALL_INFO`
    for extra detail.
  - Cross-check with `tick-cpu-stall-time` output if present.

### Possibility 6: Complete freeze — no marker in log

- Step 1 — Issue Pattern:
  - Log ends abruptly with no `BUG:` / `WARN:` / `panic:`; no reset
    line either. Serial output stops mid-word.

- Step 2 — Understand Pattern:
  - CPU wedge with all detectors disabled (watchdogs off in Kconfig,
    or fired but couldn't get to console), OR a bus/fabric hang, OR
    an interrupt-disabled infinite loop without the NMI watchdog.

- Step 3 — Correlated Pattern:
  - Look at the last few lines. If a driver was mid-transaction on
    hardware known to hang the bus, that's the primary suspect.

- Step 4 — Form Hypothesis:
  - "Freeze inside `<subsystem>` — needs external capture (JTAG,
    ramoops with pstore, kdump) to make progress."

- Step 5 — Validate Hypothesis:
  - Request a kernel build with `CONFIG_HARDLOCKUP_DETECTOR=y`,
    `CONFIG_SOFTLOCKUP_DETECTOR=y`, pstore/ramoops enabled, and
    re-capture. Do not speculate on root cause further without more
    logs.

---

## 4 — Key Notes to Remember

- Always identify **the first strong signature**, not the last. On a
  cascading crash the panic message you see at the end of the log is
  frequently downstream of a WARN or an oops printed dozens of seconds
  earlier.
- `Tainted: G W` means a prior `WARN_ON`; grep the log for `WARNING:`
  and start there.
- On arm64, `pc` is the faulting instruction; `lr` is the return
  address (i.e. the caller). Together they name the function pair.
- `x0..x7` on function entry hold the first 8 args per AAPCS64 — often
  identifiable if `pc` is at function+0..+0x10.
- If the kernel is built with `CONFIG_UNWINDER_FRAME_POINTER=n`
  (default on arm64 is `FRAME_POINTER=y`), the call trace may be
  unreliable — trust `pc`/`lr` more than deep frames.
- Never speculate about closed-source vendor driver internals from
  their `(O)` module name — call them out as suspects but note that
  root-cause analysis requires vendor cooperation.
- Once you have a hypothesis, always propose either a fix diff (when
  confident) or a **debug patch** (when uncertain) — a `pr_info`,
  `WARN_ON_ONCE`, `dyndbg` line, or `dump_stack()` at the suspect site
  is the correct next step, not "please gather more logs".
