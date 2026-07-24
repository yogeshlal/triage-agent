# cots-irq-gic-regression-bisect.md

## Issue Class

Linux kernel interrupt-subsystem failures on ARM64 (spurious
IRQ, IRQ storms, `irq N: nobody cared`, `Disabling IRQ #N`, GIC/GICv3
driver errors, workqueue lockups) AND the meta-workflow for regression
triage: given "worked on kernel vX, broken on vY" plus a symptom,
narrow the offending commit before recommending a full `git bisect`.
Bisect strategy is included here because a large fraction of kernel
kernel bugs are regressions, and IRQ-subsystem bugs are especially
frequent regression targets on ARM64 (GICv3, GICv4.1 ITS,
pseudo-NMI, PPI/SPI numbering changes).

---

## 1 — Acquire Logs

- Full kernel log with the IRQ diagnostic line + surrounding context
  (typically the last 5-30 seconds before the IRQ was disabled).
- Content of `/proc/interrupts` if the user can capture it while the
  problem is happening — the misbehaving IRQ's counter is a strong
  tell (0 = never fires; runaway = storming).
- GIC / GICv3 boot-time diagnostic lines:
  - `GIC: Using split EOI/Deactivate mode`
  - `GICv3: <N> SPIs implemented`
  - `GICv3: <N> Extended SPIs implemented`
  - `GICv3: Distributor has no Range Selector support`
  - `GICv3: no distributor detected, giving up`
  - `GICv3: Cannot enable ITS`
- Kernel command line — check for `irqchip.gicv3_nolpi=1`,
  `nokaslr`, `nohz=off`, `nosmp`, `maxcpus=`, `isolcpus=`.
- Kernel config, especially: `CONFIG_ARM_GIC_V3`,
  `CONFIG_ARM_GIC_V3_ITS`, `CONFIG_HAVE_ARM_SMCCC`,
  `CONFIG_ARM64_PSEUDO_NMI`, `CONFIG_GENERIC_IRQ_DEBUGFS`.
- Regression-bisect inputs (when user reports "used to work"):
  - Last known-good kernel version and its git SHA (from banner or
    `/proc/version`).
  - First known-broken kernel version + SHA.
  - Reproducer: a single command or workload that triggers the bug
    reliably (or "boots to shell / doesn't" as a bisect predicate).

---

## 2 — Identify if Issue Applies

- Action: Locate the IRQ / GIC / workqueue signature in the log, OR
  confirm the user has a good/bad SHA range for the regression-bisect
  branch of this CoT.

- Normal Behaviour: no `nobody cared`, no `Disabling IRQ`, no
  `GICv3: <error>`, no `BUG: workqueue lockup`.

- Abnormal Behaviour: any of the signatures below.

- Next Step: proceed only if a signature matches OR the user has a
  concrete good/bad SHA regression. If the log has an oops or a
  panic without IRQ involvement, route to
  `cots-panic-lockup-rcu-stall.md`.

### Classification Signatures (pick primary)

| Category                     | Positive signature                                                        | Primary evidence                                              |
|------------------------------|---------------------------------------------------------------------------|---------------------------------------------------------------|
| Spurious IRQ / nobody-cared  | `irq <N>: nobody cared (try booting with the "irqpoll" option)`           | `/proc/interrupts` line for that IRQ + descriptor from driver |
| Kernel disabled a storming IRQ | `Disabling IRQ #<N>`                                                    | Rate of interrupts leading up + which driver claimed it       |
| GICv3 boot error             | `GICv3: <error>` — no distributor, ITS init failure, redistributor stride | Boot log at the GIC init point                                |
| Workqueue lockup             | `BUG: workqueue lockup - pool cpus=<mask> flags=<f> nice=<n> stuck for <s>s!` | Workqueue name + last dispatched work item                    |
| IRQ affinity mismatch        | `IRQ<N>: no longer affine to CPU<n>`, `genirq: Flags mismatch irq <N>`    | `smp_affinity` mask vs online CPUs                            |
| Regression (meta)            | User states "worked on <vX> broken on <vY>" with reproducer               | Version pair + reproducer                                     |

---

## 3 — Take Steps to Analyse the Issue

### Possibility 1: Spurious IRQ — `irq N: nobody cared`

- Step 1 — Issue Pattern:
  - Line reading `irq <N>: nobody cared (try booting with the
    "irqpoll" option)` followed by a stack trace of the disable path.

- Step 2 — Understand Pattern:
  - The kernel got 100k IRQs on line `<N>` in a short window; every
    registered handler returned `IRQ_NONE`. Kernel disables the line
    to prevent lockup.
  - Two common causes: (a) the driver's ISR checks the wrong status
    register (returns `IRQ_NONE` when it should return `IRQ_HANDLED`);
    (b) the IRQ line is shared with another device and the handler
    for the real source is missing / mis-registered.

- Step 3 — Correlated Pattern:
  - `/proc/interrupts` line for `<N>` names the driver(s) that
    registered — if only one, it's a mis-classification bug;
    if multiple, it's a shared-line issue.
  - Cross-check DT: is the interrupt phandle pointing at the right
    GIC line? PPI/SPI numbering + `IRQ_TYPE_LEVEL_HIGH`/`_LOW`
    flags matter.

- Step 4 — Form Hypothesis:
  - "Driver `<D>` on IRQ `<N>` returns `IRQ_NONE` because
    `<status check>` — status bit is being cleared elsewhere OR
    is at a different offset."

- Step 5 — Validate Hypothesis:
  - Read driver's `irqreturn_t` ISR at the reported SHA.
  - Suggest a debug patch: `pr_info("status=%08x mask=%08x", …)` at
    top of ISR to see what the driver sees vs what's actually latched.

### Possibility 2: `Disabling IRQ #N` (storming)

- Step 1 — Issue Pattern:
  - `Disabling IRQ #<N>` without prior `nobody cared`, i.e. the
    handler DOES return `IRQ_HANDLED` every time but too rapidly.

- Step 2 — Understand Pattern:
  - IRQ fires ≫ 100k times/sec — often edge-triggered line with the
    hardware source not clearing, OR a level-triggered line where
    the handler forgets to clear the ACK/EOI at the peripheral end.
  - Peripheral bug is common (SPI/I2C/USB controller keeping the
    line asserted).

- Step 3 — Correlated Pattern:
  - `/proc/interrupts` shows the counter jumping thousands per read.
  - Any peripheral errata in the SoC's TRM about IRQ storming?

- Step 4 — Form Hypothesis:
  - "Peripheral `<P>` keeps line asserted because
    `<enable/clear/mask register>` is not written by the handler."

- Step 5 — Validate Hypothesis:
  - Read the ISR; compare with vendor / peer drivers on the same
    peripheral family.
  - Debug patch: log the offending status register at each entry.

### Possibility 3: GICv3 boot-time error

- Step 1 — Issue Pattern:
  - `GICv3: <specific error>` at boot, before secondary CPU bringup.

- Step 2 — Understand Pattern:
  - `no distributor detected, giving up` → DT `interrupt-parent` /
    `gic` node addresses don't match hardware.
  - `Cannot enable ITS` → `msi-parent` misconfigured OR
    `CONFIG_ARM_GIC_V3_ITS` not enabled OR ITS memory reservation
    conflicting with `reserved-memory`.
  - `Distributor has no Range Selector support` (INFO-level, not
    an error, but flags a specific hardware config).

- Step 3 — Correlated Pattern:
  - Check DT `gic` node: address ranges, `#redistributor-regions`
    property, `#interrupt-cells`.
  - Check `MSI-X`-using PCIe devices — those need ITS.

- Step 4 — Form Hypothesis:
  - "GIC node `<node>` in DT has wrong `reg` cell count OR wrong
    number of redistributor regions vs the SoC's actual layout."

- Step 5 — Validate Hypothesis:
  - Compare `dtc -I fs /sys/firmware/devicetree/base` against the
    SoC TRM.
  - Propose a DTS patch correcting the `reg` entries or the
    `#redistributor-regions` value.

### Possibility 4: Workqueue lockup

- Step 1 — Issue Pattern:
  - `BUG: workqueue lockup - pool cpus=<mask> flags=<f> nice=<n>
    stuck for <s>s!` typically at 30s, plus a dump of pending work
    items.

- Step 2 — Understand Pattern:
  - Workqueue pool's worker thread hasn't picked up any of its
    pending work in `<s>` seconds. Root causes: (a) worker is
    scheduled but blocked on a lock/completion; (b) a work item
    ran for a long time and monopolised the pool.

- Step 3 — Correlated Pattern:
  - Which work function is at the head of the pending list?
    That's the one that's either stuck or would run if the pool
    could catch up.

- Step 4 — Form Hypothesis:
  - "Workqueue `<wq>` pool has worker blocked in `<call>` while
    work items `<w1>` … `<wN>` queue up."

- Step 5 — Validate Hypothesis:
  - Cross-reference with the `INFO: task <worker>:<pid> blocked`
    line (usually present alongside) — that gives the worker's
    call stack. Route to `cots-panic-lockup-rcu-stall.md`
    Possibility 4 for hung-task analysis of the worker itself.

### Possibility 5: Regression bisect (meta-workflow)

- Step 1 — Issue Pattern:
  - User says: "boots fine on `<vX>`, panics/hangs/regresses on
    `<vY>` with the same config and same DT and same reproducer".

- Step 2 — Understand Pattern:
  - This is not a log-analysis problem — it's a commit-hunt
    problem. The bisect strategy has three stages:
    - **(a) Narrow analytically** — before running any bisect,
      constrain the range using the crash signature.
    - **(b) Predict statically** — apply the `soft-bisect` skill
      (`skills/devops/soft-bisect/`) which scores every commit in
      the range against the signature without executing anything.
    - **(c) Confirm dynamically** — write a `git bisect run`
      reproducer and let git iterate over the predicted top-N.

- Step 3 — Correlated Pattern:
  - Extract from the log:
    - Suspect subsystem (from Call trace top function or
      `<driver>: probe of` line).
    - Config option involved (from Kconfig or module name).
    - Config-independent baseline (does `defconfig` show the same
      regression, or only user's config).

- Step 4 — Form Hypothesis:
  - Narrowed range: `git log --oneline <good_sha>..<bad_sha> --
    <suspect_paths>` produces a shortlist of maybe 20–200 commits.
    Hand off to `soft-bisect`:
    - Provide the crash signature (top of Call trace, or the
      first strong log line).
    - Provide `good_sha`, `bad_sha`, and any subsystem paths from
      Step 3 to weight results.
    - `soft-bisect` returns ranked suspects with confidence scores.

- Step 5 — Validate Hypothesis:
  - Write a `git bisect run` reproducer. Skeleton:
    ```sh
    #!/bin/sh
    # bisect-reproducer.sh — exit 0 if kernel is GOOD, exit 1 if BAD, exit 125 to skip
    set -e
    make -j"$(nproc)" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- Image dtbs
    # optionally: install into a boot menu, kexec, or QEMU boot
    # then run the user's reproducer and check for the crash signature
    if timeout 60 sh -c "<user reproducer>" 2>&1 | grep -q "<signature>"; then
        exit 1  # BAD — regression reproduced
    else
        exit 0  # GOOD
    fi
    ```
  - Kick off:
    ```
    git bisect start <bad_sha> <good_sha>
    git bisect run ./bisect-reproducer.sh
    ```
  - Cross-check the `git bisect` result against `soft-bisect`'s
    top-1: they should agree, and disagreement means either the
    reproducer is non-deterministic or the signature is
    downstream of the actual regressing commit.

---

## 4 — Key Notes to Remember

- IRQ storms and `nobody cared` are usually driver bugs, not core
  IRQ-subsystem bugs. Focus on the peripheral driver first.
- Level-vs-edge triggering (`IRQ_TYPE_LEVEL_HIGH` vs
  `IRQ_TYPE_EDGE_RISING`) in DT must match the hardware and the
  driver's expectation. Mismatch is a classic source of `nobody
  cared` and IRQ storms.
- GICv3 issues visible only at boot are almost always DT / firmware
  config bugs, not GIC driver bugs. The driver is stable.
- `BUG: workqueue lockup` is a symptom — always chase the worker's
  actual blocking site. Route to `cots-panic-lockup-rcu-stall.md`.
- For regression bisect: the shortest path to root cause is
  **narrow first, predict second, run bisect third**. Users
  frequently jump to `git bisect run` on the full range without
  narrowing — that wastes many kernel builds.
- Prefer `soft-bisect` (static prediction) over blind
  `git bisect run` when: (a) each build takes > 5 min, (b) the
  reproducer is flaky, (c) the range is > 500 commits.
- When handing off to `soft-bisect`, always pass the crash
  **signature** (the exact string that appears in the log at
  failure) — that is the strongest scoring input the skill uses.
- Never propose "add a WARN_ON to bisect" as a fix — that's a
  workaround, not a solution.
- Kernel-command-line workarounds (`irqpoll`, `noapic`,
  `nolapic_timer`, `pci=nomsi`) are diagnostic tools, not
  production fixes.
