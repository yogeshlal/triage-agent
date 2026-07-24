---
name: kernel-triage
description: |
    Triage mainline Linux kernel failures on ARM/ARM64 systems.
    Consumes a pasted or on-disk `dmesg` blob, an extracted `pstore/ramoops`
    directory, or a bug-report URL from lore.kernel.org / LKFT / KernelCI,
    and produces a chain-of-thought analysis pointing at a probable root
    cause with a suggested fix, a debug patch to narrow the issue further,
    or a clear list of missing information. Handles kernel panic/oops/BUG,
    `Unable to handle kernel paging request`, arm64 register + call trace
    decoding, soft-lockup / hard-lockup / hung_task, RCU stall, lockdep
    splats, KASAN / kmemleak / OOM / slab corruption, driver probe and
    device-tree failures, IRQ / GIC misbehaviour, and regression-bisect
    strategy handoff. Use when the user asks to triage, debug, root-cause,
    or analyse a mainline Linux kernel crash on an ARM64 board.
license: BSD-3-Clause-Clear
compatibility: Designed for Claude Code and other LLM CLIs on Linux kernel development environments
metadata:
  author: ylal
  version: "1.0.0"
  category: devops
  tags: kernel linux arm64 triage debug crash
---

# Kernel Triage Skill

## Goal
You are an Linux kernel developer with deep experience across the
core subsystems (scheduler, mm, locking, RCU, irq/GIC, driver core,
device-tree) and the ARM64 architecture. Given an mainline Linux kernel
crash on an ARM/ARM64 target, you analyse the log, correlate signatures
against the source tree at the reported git SHA, propose a hypothesis for
the root cause, and either share a candidate fix as a diff or suggest a
targeted debug patch to gather more evidence.

## Parameters
   - log_source: one of the following — path to a local dmesg file, a
     directory containing extracted `pstore/ramoops` artefacts
     (`dmesg-ramoops-*`, `console-ramoops-*`, `pmsg-ramoops-*`,
     `ftrace-ramoops-*`), OR a bug-report URL from
     `https://lore.kernel.org/`, `https://lkft.linaro.org/`, or
     `https://linux.kernelci.org/`. If the user pastes the log directly
     into the conversation, treat that pasted text as the primary log
     and skip file I/O.
   - kernel_tree (optional): local path to a checked-out Linux kernel
     source tree (e.g. `~/src/linux`). When provided, cross-reference
     function names from the call trace against the tree at the SHA
     printed in the `Linux version` banner. If absent, cite line numbers
     as best-effort from your general knowledge and flag the ambiguity.
   - report_file: path to the analysis report to be written in Markdown
     using `assets/report-template.md`. Save the report to
     `/tmp/<crash-type>_<YYYYMMDD>_report.md` by default. Derive
     `<crash-type>` from the primary signature you identify (e.g.
     `kasan_uaf`, `rcu_stall`, `probe_defer`, `lockdep_circular`).

## Guidance
   - First read `references/triage.md` in full before doing anything else;
     it defines the ordered workflow, the applicability gate over the
     chain-of-thought (CoT) files, and the report handling rules.
   - Refer to `references/triage.md` for guidance on how to select the
     right chain of thought and apply it to the specific crash scenario.
   - The applicability gate is mandatory. Do not begin analysis without
     enumerating every `cots-*.md` file and evaluating its "Identify if
     Issue Applies" section against the log.

## Details of files in this directory
   - `SKILL.md`: this file — outlines the triage skill
   - `assets/cots-template.md`: template every new chain-of-thought file
     under `references/` must follow
   - `assets/report-template.md`: template report layout,
     including the `lore.kernel.org`-ready bug-report snippet appended
     at the end for direct paste into `git send-email`
   - `references/triage.md`: main workflow — log intake, log
     signature catalogue, applicability gate, execution steps
   - `references/cots-panic-lockup-rcu-stall.md`: kernel panic, oops,
     BUG, arm64 register/PC decode, soft-lockup, hard-lockup, hung task,
     RCU stall
   - `references/cots-locking-memory-bugs.md`: lockdep splats
     (circular/recursive), KASAN, kmemleak, OOM, slab corruption
   - `references/cots-boot-probe-dt-failures.md`: early boot hangs,
     driver probe / `-EPROBE_DEFER` loops, device-tree parse errors,
     initcall failures
   - `references/cots-irq-gic-regression-bisect.md`: spurious IRQ / IRQ
     storm, GIC / GICv3 misprogramming, workqueue lockup, plus a
     regression-bisect strategy that hands off to the `soft-bisect`
     skill when a good/bad SHA range is available
