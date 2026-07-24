# cots-boot-probe-dt-failures.md

## Issue Class

Linux kernel boot-time and probe-time failures on ARM64: no
console output at all (early hang before earlycon or with a wrong
`console=` param), driver probe failures with negative errno, endless
`-EPROBE_DEFER` loops, device-tree parse errors (bad DTB, missing
`compatible`, misspelt properties), and initcall failures visible via
`initcall_debug`. These are distinct from run-time crashes: the kernel
either never reached a shell, or a specific driver silently didn't
bind, and the "log" is often just a truncated boot log with no oops.

---

## 1 — Acquire Logs

- Boot console output — the full log from power-on (or `kexec`) to
  either the last console line or the point where userspace was
  supposed to start.
- The `Kernel command line: <args>` line — absolutely essential for
  boot debugging. Check for:
  - `earlycon` (or `earlycon=<name>,<addr>`) — enables printk before
    the real console is up. Absence explains "no output before boot
    banner".
  - `console=<tty>,<baud>` — must match the physical UART.
  - `loglevel=<n>` — `loglevel=7` (or `debug` / `initcall_debug`)
    increases verbosity.
  - `initcall_debug` — logs every initcall entry/exit with timing.
  - `no_console_suspend` — keeps console alive during suspend.
  - `keep_bootcon` — retains earlycon after console handover.
- Device-tree state:
  - Boot log's `Machine model: <string>` (from DT `/model`) and
    `Hardware name: <string>` — confirm the right DTB was loaded.
  - If the user can run `ls /sys/firmware/devicetree/base/`, that's
    the live DT view.
  - `dtb` or `dtc -I fs /sys/firmware/devicetree/base` output if
    dumped.
- initcall log with `initcall_debug`:
  `calling  <fn>+0x0/0x<size> @ <pid>` / `initcall <fn>+0x…/0x…
  returned <errno> after <usecs> usecs`.
- driver_probe log lines:
  `<subsystem>: driver <name> probe of <device> failed with error <-N>`
  / `deferred probe pending: <driver>: (reason?)` /
  `platform <dev>: Driver <driver> requests probe deferral`.
- Kernel config values that matter for boot:
  - `CONFIG_EARLY_PRINTK` / `CONFIG_SERIAL_<UART>_CONSOLE`.
  - `CONFIG_INITCALL_LEVEL_DEBUG`, `CONFIG_PRINTK_TIME=y`,
    `CONFIG_DYNAMIC_DEBUG=y`.

---

## 2 — Identify if Issue Applies

- Action: Distinguish between the three sub-classes below by looking
  at where the boot log ends and what the last driver-related line is.

- Normal Behaviour: kernel reaches `Freeing unused kernel image
  memory` → `Run /init as init process` → userspace starts; every
  driver either binds successfully or is genuinely absent from the DT.

- Abnormal Behaviour: any of the classification signatures below.

- Next Step: proceed with analysis only if issue applies; if the log
  has a proper panic/oops, route to `cots-panic-lockup-rcu-stall.md`.

### Classification Signatures (pick primary)

| Category                          | Positive signature                                                                             | Primary evidence                             |
|-----------------------------------|------------------------------------------------------------------------------------------------|----------------------------------------------|
| No console output at all          | Log is empty or ends at boot loader                                                            | Absence of `Linux version` line              |
| Early hang (banner shows, then stops) | `Linux version …` printed then log ends before `Freeing unused kernel image memory`         | Log tail without oops                        |
| DT parse failure                  | `Bad device tree`, `unrecognized OF match`, `of_iomap: no reg`, `unflatten_dt_alloc failed`    | libfdt error lines                           |
| Driver probe failure              | `<driver>: probe of <dev> failed with error -<N>` (finite, non-`-517`)                         | Errno with function calls preceding          |
| EPROBE_DEFER loop                 | Repeated `Driver … requests probe deferral` for same driver > 10× or `deferred probe pending`  | Dependency driver never appears              |
| initcall failure                  | `initcall <fn>+0x…/0x… returned -<N> after <us> usecs` (visible with `initcall_debug`)         | Non-zero return from a `*_initcall(fn)`       |

---

## 3 — Take Steps to Analyse the Issue

### Possibility 1: No console output at all

- Step 1 — Issue Pattern:
  - User reports the board doesn't boot. Serial capture is empty or
    stops at the bootloader (u-boot / TF-A / GRUB).

- Step 2 — Understand Pattern:
  - Kernel may be running but not talking to the console because
    `earlycon` isn't set, or the `console=<tty>` doesn't match the
    UART actually wired.
  - Alternative: kernel is not running because MMU/exception setup
    faulted (very early crash pre-printk).

- Step 3 — Correlated Pattern:
  - Ask user for the `chosen/stdout-path` value from the DT — that
    is the default console on arm64 if `console=` is absent.
  - Compare `chosen/stdout-path` vs the UART the user is actually
    monitoring.

- Step 4 — Form Hypothesis:
  - "Kernel runs but console output is going to `<other UART>`
    because `chosen/stdout-path` points there and no `console=`
    override was passed."

- Step 5 — Validate Hypothesis:
  - Ask user to boot with `earlycon` (bare, no arg — reads
    `stdout-path`) and `console=<their-uart>,<baud>` on the cmdline.
  - If earlycon still shows nothing, escalate to hardware setup —
    kernel image build for the wrong `--endian`, wrong
    `Image`-vs-`Image.gz`, wrong load address, wrong
    `crashkernel=` reservation.

### Possibility 2: Early hang after banner

- Step 1 — Issue Pattern:
  - `Linux version …` line prints, a handful more lines print, then
    output stops without oops or panic.

- Step 2 — Understand Pattern:
  - Kernel is progressing through `setup_arch` → early alloc → SMP
    boot when it stops. Common early-hang causes on arm64:
    - PSCI CPU_ON failing silently on secondary CPUs.
    - Broken clock/regulator descriptor in the DT causing
      infinite deferrer loop very early.
    - Faulty memory map (`memblock_add` errors) causing subsequent
      init to loop.
    - `earlycon` still active but real console handover deadlocked.

- Step 3 — Correlated Pattern:
  - Look at the last line before the hang. `smp: Bringing up
    secondary CPUs ...` with no follow-up `Detected VIPT I-cache
    on CPU<n>` lines → PSCI CPU_ON hanging.
  - `Booting Linux on physical CPU 0x…` alone → primary CPU stuck
    in the very early path, likely MMU / stack setup.

- Step 4 — Form Hypothesis:
  - Pick one of: (a) SMP init hang → look at PSCI node and TF-A
    build; (b) memory init hang → check `memory@…` node and
    `reserved-memory` regions; (c) clock/regulator missing → will
    also show up as EPROBE_DEFER, hand off to Possibility 4.

- Step 5 — Validate Hypothesis:
  - Ask for `nosmp` boot to test hypothesis (a) — if it boots to
    userspace on 1 CPU, SMP init is the bug.
  - Ask for `earlyprintk` output with `debug` cmdline flag.

### Possibility 3: DT parse failure

- Step 1 — Issue Pattern:
  - `Bad device tree`, `unrecognized OF match`, `of_iomap: no reg
    for <node>`, `unflatten_dt_alloc failed`, or FDT-related panic
    early in boot.

- Step 2 — Understand Pattern:
  - The DTB the kernel received is malformed or has a node the
    kernel binding doesn't understand.
  - `of_iomap: no reg` = a driver called `of_iomap()` on a node
    lacking a `reg` property.
  - `unrecognized OF match` = binding table doesn't include the
    `compatible` string; usually a fresh DTB against an old kernel.

- Step 3 — Correlated Pattern:
  - Check DT `model` vs the board the user says they have — a
    common bug is loading the wrong DTB via u-boot's
    `${fdtfile}` / `${fdt_addr}`.
  - Check `dtc` version used to build the DTB — very new DTS syntax
    (property overrides, `/omit-if-no-ref/`) built with old
    `libfdt` in the kernel can produce `Bad device tree`.

- Step 4 — Form Hypothesis:
  - "DTB from `<user's build>` uses `<feature>` unsupported by the
    kernel's libfdt version; either regenerate with matching `dtc`
    or backport the libfdt update."

- Step 5 — Validate Hypothesis:
  - Ask for `fdtdump <path/to/dtb> | head -50` to confirm structure.
  - If binding is missing, propose either: (a) send a patch adding
    the binding to `Documentation/devicetree/bindings/…` and the
    driver's `of_match_table`, or (b) use the compatible fallback
    string if one exists in the binding.

### Possibility 4: EPROBE_DEFER loop

- Step 1 — Issue Pattern:
  - Repeated `Driver <A> requests probe deferral` for the same
    driver, OR `deferred probe pending: <A>` at boot completion,
    OR the same driver never printing a `<A>: probe succeeded`.

- Step 2 — Understand Pattern:
  - Driver A's `probe()` calls `<get-something>()` (e.g.
    `devm_clk_get`, `devm_regulator_get`,
    `of_parse_phandle_with_args`) that returns `-EPROBE_DEFER`
    because dependency B hasn't probed yet.
  - Normal case: B probes, then A re-probes and succeeds.
  - Bug case: B never probes (missing driver / disabled node /
    wrong compatible) so A defers forever.

- Step 3 — Correlated Pattern:
  - Find the DT node for A. Which phandle references were unresolved?
  - Which driver is expected to bind to the pointed-at node? Is
    that driver present in the kernel image? Is `CONFIG_<X>=y`?

- Step 4 — Form Hypothesis:
  - "Driver A defers waiting for `<resource>`. That resource is
    provided by driver B on node `<node>` which is
    `status = "disabled"` OR whose Kconfig is disabled OR whose
    compatible has no matching driver."

- Step 5 — Validate Hypothesis:
  - Check `/sys/kernel/debug/devices_deferred` if user can query it.
  - Ask for `dmesg | grep -E "(defer|probe)"` — pattern of who
    defers on whom tells the dependency graph.
  - Fix by enabling the missing config, correcting the compatible,
    or promoting the disabled node to `status = "okay"`.

### Possibility 5: Explicit driver probe failure (non-defer errno)

- Step 1 — Issue Pattern:
  - `<driver>: probe of <dev> failed with error <-N>` where `<-N>`
    is a fixed errno (not `-517 / -EPROBE_DEFER`).

- Step 2 — Understand Pattern:
  - Common errnos:
    - `-2 -ENOENT` — DT node missing required property or child.
    - `-12 -ENOMEM` — allocator failed (rare at probe time).
    - `-19 -ENODEV` — device not present per driver's own check.
    - `-22 -EINVAL` — property in DT is invalid (out-of-range, wrong
      type).
    - `-38 -ENOSYS` — feature not compiled in.
    - `-95 -EOPNOTSUPP` — hardware variant not supported.
    - `-110 -ETIMEDOUT` — HW init handshake timed out.

- Step 3 — Correlated Pattern:
  - The line immediately preceding the "probe of failed" is usually
    the specific error from within probe (e.g.
    `<driver>: failed to get clock: -22`). That is the actual
    diagnostic.

- Step 4 — Form Hypothesis:
  - Root-cause is the specific errno-producing call. Trace to its
    source in the driver.

- Step 5 — Validate Hypothesis:
  - Read the driver source at the reported SHA; identify the
    failing `<fn>()` call; validate against the DT node.
  - Propose fix: correct the DT property, add a missing
    `<property>`, or (if driver bug) fix the check.

### Possibility 6: initcall failure

- Step 1 — Issue Pattern:
  - `initcall_debug` output: `initcall <fn>+0x…/0x… returned -<N>
    after <us> usecs`.

- Step 2 — Understand Pattern:
  - `<fn>` is the initcall function (e.g. `sysctl_init_bases`,
    `<subsystem>_init`). Non-zero return often surfaces late as
    "feature not present" or a driver never registering.

- Step 3 — Correlated Pattern:
  - Match the errno to the specific check inside `<fn>`.

- Step 4 — Form Hypothesis:
  - "initcall `<fn>` returned `-<N>` because `<internal check>`
    failed; downstream driver / subsystem A relies on this and
    silently doesn't work."

- Step 5 — Validate Hypothesis:
  - Read `<fn>` source; propose fix at the failing check.

---

## 4 — Key Notes to Remember

- Boot failures without a panic are notoriously log-poor. Always ask
  for `earlycon` + `initcall_debug` + `loglevel=7` on the cmdline as
  the first move if the current capture is insufficient.
- `-EPROBE_DEFER` (`-517`) is not an error in isolation — it's the
  normal probe-ordering mechanism. Only treat it as a bug if the
  driver defers forever (visible via
  `/sys/kernel/debug/devices_deferred` or repeated log lines).
- `Machine model:` and `Hardware name:` come from DT `/model` and
  `/compatible` — mismatch with the target board = wrong DTB.
- On arm64, `Booting Linux on physical CPU 0x0000000000` is the first
  line printed by `smp_setup_processor_id()` — its presence proves
  the MMU + printk are up.
- Do not confuse "no console output" with "no boot". Ask the user
  what UART they're monitoring and what `chosen/stdout-path`
  points at.
- For DT bugs, always propose the fix in DTS form (a diff on the
  `.dts` file), not on the compiled `.dtb`.
- Don't recommend `pr_info` sprinkling as the first debug move —
  request `initcall_debug` + `earlycon` first; those are already-
  compiled diagnostics.
