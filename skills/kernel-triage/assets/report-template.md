# Linux Kernel Triage Report Template

## Add a Descriptive Title of the Linux Kernel Issue

## Executive Summary

**Crash Type:** One-line summary of the primary signature (e.g.
`KASAN use-after-free in tcp_v4_rcv`, `RCU stall on CPU 3`,
`Driver probe of foo failed with -EPROBE_DEFER`, `arm64 kernel NULL
pointer dereference in dwc3_gadget_ep_queue`)
**LAVA Job URL:** Full URL of the LAVA job page (e.g.
`https://lava.example.org/scheduler/job/12345`). Do not use the bare
numeric id — always the full URL so reviewers can open it directly.
**Platform:** SoC + board name from DT `model` / `Hardware name:` line
**Kernel Version:** From `Linux version …` banner including any
`-g<sha>` suffix
**Kernel Config:** Base defconfig name if identifiable (e.g.
`arm64/defconfig`, `<vendor>_defconfig`) + any custom `CONFIG_*`
options relevant to the report (e.g. `CONFIG_KASAN=y`,
`CONFIG_PROVE_LOCKING=y`)
**Toolchain:** Compiler string from the banner
(e.g. `gcc 13.2.0`, `clang 17.0.0`)
**Crash Time:** Timestamp from the log
(`[    N.NNNNNN]` or absolute if `printk.time=1`)
**Root Cause:** One-line summary of the identified root cause (or
"undetermined — see Additional Information Needed" if not yet
localised)
**Log Source:** Where the log came from — `pasted stdin`,
`/path/to/file`, `pstore/ramoops directory`, or the URL of the
LKFT / lore.kernel.org / KernelCI report

## Applicability Gate

Mandatory table produced by running Step 1 of `references/triage.md`.
Every `cots-*.md` file in `references/` must appear:

| CoT Doc                                    | Applies? (Y/N) | Evidence (log source + exact matching string) |
|--------------------------------------------|----------------|-----------------------------------------------|
| `cots-panic-lockup-rcu-stall.md`           | Y / N          | e.g. `dmesg:12345: Kernel panic - not syncing: ...` |
| `cots-locking-memory-bugs.md`              | Y / N          | e.g. `dmesg:12100: BUG: KASAN: use-after-free in ...` |
| `cots-boot-probe-dt-failures.md`           | Y / N          | e.g. `no matching signature`                  |
| `cots-irq-gic-regression-bisect.md`        | Y / N          | e.g. `no matching signature`                  |

For every doc marked N, write 1–2 sentences justifying elimination
citing the missing / present signature.

## Crash Signature Analysis

### Primary Issue Indicators

Present the primary indicators found by the applicability gate.
Quote the exact log lines verbatim in fenced code blocks so future
readers can grep-verify.

### Key Observations

Additional observations from cross-correlating the log with other
artefacts (`Modules linked in:`, `Tainted:`, `Hardware name:`,
`Kernel command line:`, other CPU backtraces, ftrace ring buffer).

## Detailed Technical Analysis

Chain-of-thought stitched narrative. For each `cots-*.md` marked Y in
the applicability gate, walk through its Steps 3.1–3.5 (Issue Pattern
→ Understand → Correlated Pattern → Form Hypothesis → Validate
Hypothesis) using this specific log. Cite the exact log lines and,
where relevant, source paths at the kernel SHA from the banner.

## System State at Crash

**CPU States (all cores present in the log):** which task / function
each CPU was in at the time of the primary signature, plus its call
trace if dumped.

- CPU0:
- CPU1:
- CPU2:
- CPU3:
- CPU4:
- CPU5:
- CPU6:
- CPU7:

**Memory State:**

- Total / free RAM (from `Mem-Info:` if the log has an OOM block).
- Zone breakdown (`Zone: DMA / Normal / Movable`) if present.
- Per-zone free per order (`buddyinfo`) if the log has it.
- Slab summary line(s) if present.
- Any `HugePages` / CMA activity if relevant.

**Pre-Crash Activity:**

- Last ~50 log lines before the primary signature.
- ftrace-ramoops-0 highlights if present.
- `initcall_debug` timing anomalies if present.

## Root Cause Analysis

Based on the detailed analysis, state the root cause. If not fully
identified, state that clearly and explain what evidence is missing;
the fix / debug patch will accordingly be either targeted at the
root cause or aimed at gathering the missing evidence.

### Sequence of Events

Numbered timeline from the earliest anomaly in the log to the
primary signature.

### Contributing Factors

Configuration, hardware, timing, or interaction factors that made
this bug reproducible in this specific case (e.g. "specific to
`CONFIG_PREEMPT=y`", "only visible with `CONFIG_KASAN=y`",
"race window opened by `nohz=off`").

## Potential Causes

**Cause:**
Enumerate the potential root causes (usually one is high-confidence,
alternates are listed for completeness).

**Evidence:**
For each cause, cite the corresponding evidence (log lines, source
lines, DT properties).

## Recommendations

Fix recommendations based on the root-cause analysis. Provide either
a candidate fix (unified diff, ready to `git apply`) or a targeted
debug patch to gather the missing evidence.

### Immediate Actions

Actions the user can take now to mitigate the issue without the full
fix (e.g. cmdline workaround, disable a Kconfig, revert the
suspected commit).

### Code Areas to Investigate

Files / functions / subsystems that warrant closer reading — cite
paths relative to the kernel source root at the reported SHA.

### Debugging Steps

Concrete next steps if the root cause is not conclusive: which
diagnostic to enable (`CONFIG_KASAN`, `CONFIG_LOCKDEP`,
`initcall_debug`, `dyndbg`), which reproducer to run, which log to
capture next.

### Proposed Fix

The candidate fix as a unified diff. Include the file path, the
context lines, and a subject-line-quality one-liner explaining the
change. Example:

```diff
--- a/drivers/foo/foo_core.c
+++ b/drivers/foo/foo_core.c
@@ -123,6 +123,9 @@ static int foo_probe(struct platform_device *pdev)
     if (!priv)
         return -ENOMEM;

+    if (!pdev->dev.of_node)
+        return -ENODEV;
+
     priv->clk = devm_clk_get(&pdev->dev, "core");
```

If the fix requires more than a small hunk, provide a single logical
patch; do not bundle unrelated changes.

## Additional Information Needed

If the analysis cannot conclude root cause, list exactly what more
information is required — kernel config, `earlycon` output,
`/proc/interrupts` snapshot, KASAN build, reproducer script.

## References

Bulleted list of every log location or source path cited in the
report. One line each. Include:

- Log lines and their line-number ranges.
- Kernel source paths at the reported SHA.
- Any lore.kernel.org, LKFT, or KernelCI URL the user supplied.
- Any Documentation/ files referenced.

---

## Conclusion

**Priority:** Low / Medium / High
**Security Impact:** Low / Medium / High
**Reproducibility:** Always / Sometimes / Once / Unknown

---

## Lore-ready Bug Report Snippet

The following block is generated for direct paste into
`git send-email` or into a fresh message on the relevant subsystem
mailing list at `https://lore.kernel.org/`. Every field is filled
from the analysis above — replace nothing with placeholders.

```
Subject: [BUG] <subsystem>: <one-line summary of the crash>

Hi <maintainer(s)>,

I hit the following Linux kernel crash on an ARM64 target.

  * Kernel version: <ver>-g<sha> built from <tag/branch> on <toolchain>
  * Config: <defconfig> + <notable custom options>
  * Hardware: <SoC / board string from DT model>
  * Reproducer:
      <one-line command or a numbered sequence of steps that reliably
       reproduces; include workload/kselftest/syzkaller repro if any>

Trimmed backtrace (first strong signature):

  <verbatim quoted lines from the log; keep this compact — one WARN /
   BUG / oops block, not the entire dmesg>

Full dmesg attached below (or linked at <URL if applicable>).

<Short paragraph describing the analysis and hypothesis, referencing
the exact function/offset the fault happens at, and pointing at the
suspected commit if this is a regression.>

Suggested fix / debug patch attached inline (or see [1]).

Reported-by: <Reporter Name> <reporter@example.org>
<if regression:> Fixes: <12-char-sha> ("<subject of the suspect commit>")
<if stable-worthy:> Cc: stable@vger.kernel.org # <first stable series that has the bug>

Thanks,
<Reporter Name>

--
[1] <link to reproducer / lkft job / kernelci run, if any>
```

**Reminders when using the snippet:**
- Use `./scripts/get_maintainer.pl -f <path/to/file>` from the kernel
  tree to fill in the recipient list before sending.
- Attach the full `dmesg` inline (not as an attachment) — mailing
  lists generally strip attachments.
- Match the subject prefix conventions of the target list
  (`[PATCH]` for a fix, `[BUG]` / `[REGRESSION]` for a report,
  `[RFC]` for an untested proposal).

---

**Analysis Date:**
**Analyzer:**
