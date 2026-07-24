## <Subject Clearly Indicating the Linux Kernel Issue This Chain of Thought Applies to>

1. Acquire Logs
   <List every log or artefact this CoT needs from an ARM64 Linux
   kernel crash — dmesg blob, pstore/ramoops directory, /proc/interrupts
   snapshot, kernel command line, `Modules linked in:` and `Tainted:`
   flags, kernel config values that gate the diagnostic, etc.>

2. Identify if Issue Applies
   <State the exact log signatures that trigger this CoT and the
   signatures that eliminate it. Prefer verbatim substrings from
   printk / WARN / BUG lines.>

   - Action: <which log source and pattern to grep for>
   - Normal Behaviour: <what a healthy log looks like>
   - Abnormal Behaviour: <exact failure signatures — copy from real
     public reports where possible>
   - Next Step:
      - Proceed further with analysis only if the issue applies
      - If logs are insufficient (missing diagnostic block, no kernel
        version banner, no Call trace), clearly report which artefact
        is needed and do NOT proceed with speculation

3. Take Steps to Analyse the Issue

Iterate over the following possibilities to establish the best viable
root cause and present a solution:

    - Possibility 1:
        - Step 1 - Issue Pattern:
           - <what log signature to look for>
        - Step 2 - Understand Pattern:
           - <what Linux kernel behaviour this signature represents
             — reference the responsible subsystem file (e.g.
             kernel/rcu/tree_stall.h, mm/slub.c) if useful>
        - Step 3 - Correlated Pattern:
           - <does this match a known known bug family — link
             lore.kernel.org threads or LKML fix commits if you can
             cite them without guessing URLs>
        - Step 4 - Form Hypothesis:
           - <what hypothesis applies and how to cross-check by
             reading the source at the SHA from the kernel version
             banner>
        - Step 5 - Validate Hypothesis:
           - <how to confirm — either an existing debug-config the
             user should enable (KASAN, LOCKDEP, DEBUG_KMEMLEAK,
             initcall_debug), or a targeted debug patch (pr_info,
             WARN_ONCE, dump_stack)>
    - Possibility 2:
        - Step 1 - Issue Pattern:
           - <as above>
        - Step 2 - Understand Pattern:
           - <as above>
        - Step 3 - Correlated Pattern:
           - <as above>
        - Step 4 - Form Hypothesis:
           - <as above>
        - Step 5 - Validate Hypothesis:
           - <as above>
    - <As many possibilities as apply>:
        - Step 1 - Issue Pattern:
           - <as above>
        - Step 2 - Understand Pattern:
           - <as above>
        - Step 3 - Correlated Pattern:
           - <as above>
        - Step 4 - Form Hypothesis:
           - <as above>
        - Step 5 - Validate Hypothesis:
           - <as above>

4. Key Notes to Remember
<Any exception, convention, or key piece of information the
analysis must keep in mind — for example, "the first strong signature
wins", "prefer KASAN over slab_debug", "-EPROBE_DEFER is not an error
in isolation", "on arm64, `pc` is the faulting instruction and `lr`
is the caller">
