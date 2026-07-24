# cots-locking-memory-bugs.md

## Issue Class

Linux kernel locking and memory-safety failures on ARM64:
lockdep splats (`WARNING: possible circular locking dependency
detected`, recursive locking, non-static key), KASAN reports
(use-after-free, slab/stack/global out-of-bounds, null-ptr-deref),
kmemleak reports, OOM killer invocations, and SLUB/SLAB corruption
diagnostics (`Redzone overwritten`, `Poison overwritten`,
`Freepointer corrupt`). These bugs are diagnosed by *kernel debug
infrastructure* (KASAN, lockdep, kmemleak, slab_debug), so the report
format is highly structured — parse it, don't just quote it.

---

## 1 — Acquire Logs

- Full kernel log with the diagnostic report in-place. For KASAN, the
  report is a self-contained block from `==================================================================`
  to the next `======` line — capture the whole block.
- Kernel build banner + KASAN mode (if visible):
  - `CONFIG_KASAN_GENERIC` → shadow-memory-based, works on all arm64.
  - `CONFIG_KASAN_SW_TAGS` → hardware-tagged, `-mllvm -hwasan-…`.
  - `CONFIG_KASAN_HW_TAGS` → arm64 MTE, requires FEAT_MTE hardware.
- `Modules linked in:` and `Tainted:` (same as panic CoT).
- For OOM: `Mem-Info:` block plus `Node <n> <Zone> free:...`, followed
  by the killed process's oom_score and the process list.
- For lockdep: the full circular / recursive report, including the
  `-> #<n> (<lock>){<class>}<state>:` chain entries.
- For kmemleak: `unreferenced object 0x<hex> (size <n>):` followed by
  the allocation backtrace and the `hex dump:` block.
- For slab corruption: `SLUB: kmem_cache_<name>: <Redzone|Poison>
  overwritten at offset <n>` plus surrounding `Object dump:` and
  `Freelist ` info.
- Kernel config, if the user can share it — check that the diagnostic
  responsible for the report is actually enabled (surprising number of
  reports come from users unaware of what CONFIG option produced them).

---

## 2 — Identify if Issue Applies

- Action: Locate the diagnostic report block(s) in the log. Multiple
  KASAN reports for the same address on different CPUs are the same
  bug — collapse to the first one.

- Normal Behaviour: kernel debug infrastructure is silent; no
  `====...` KASAN separator, no `WARNING: possible <> locking`, no
  `kmemleak:`, no `Out of memory:`.

- Abnormal Behaviour: any of the classification signatures below.

- Next Step:
  - Proceed only if a diagnostic block is present. If a user reports
    "the kernel oopsed" but no KASAN / lockdep block is in the log,
    this CoT does not apply — route to `cots-panic-lockup-rcu-stall.md`.

### Classification Signatures (pick primary)

| Category                     | Positive signature                                                                       | Primary evidence                                                       |
|------------------------------|------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| lockdep — circular (ABBA)    | `WARNING: possible circular locking dependency detected`                                 | Chain: `-> #<n> (<lock>)…`, other CPU acquiring same locks in reverse  |
| lockdep — recursive          | `WARNING: possible recursive locking detected`                                            | Same lock acquired twice on same task without nested class             |
| lockdep — non-static key     | `INFO: trying to register non-static key`                                                | Function that init'd the lock — often stack lock or dynamic without `_init` |
| lockdep — irq inversion      | `WARNING: SOFTIRQ-safe -> SOFTIRQ-unsafe lock order detected`                            | Locks acquired in IRQ context vs process context in different orders   |
| KASAN — use-after-free       | `BUG: KASAN: use-after-free in <fn>`                                                     | "Freed by task <t>:" + "Allocated by task <t>:" stacks                 |
| KASAN — slab-out-of-bounds   | `BUG: KASAN: slab-out-of-bounds in <fn>`                                                 | "The buggy address belongs to the object at …" size vs offset          |
| KASAN — stack/global OOB     | `BUG: KASAN: <stack|global>-out-of-bounds in <fn>`                                       | Stack frame layout or global-var name from allocator report            |
| KASAN — null-ptr-deref       | `BUG: KASAN: null-ptr-deref in <fn>`                                                     | Accessed offset within first 4 KiB of address 0                        |
| kmemleak                     | `kmemleak: <N> new suspected memory leaks`                                               | Per-object `unreferenced object` + allocation backtrace                |
| OOM killer                   | `Out of memory: Killed process <pid> (<comm>)` OR `oom-kill:`                            | `Mem-Info:` + zone free counters + oom_score                           |
| Slab corruption (SLUB debug) | `SLUB: kmem_cache_<name>: <Redzone|Poison|Freepointer> overwritten`                      | `Object dump:` + first-mismatch byte + `slab_debug=` args on cmdline    |

---

## 3 — Take Steps to Analyse the Issue

### Possibility 1: KASAN use-after-free

- Step 1 — Issue Pattern:
  - `BUG: KASAN: use-after-free in <fn>+0x<off>/0x<size>` followed by
    "Read/Write of size <n> at addr <hex> by task <comm>/<pid>", then
    the accessing stack, then "Allocated by task <ta>/<pa>:" (with
    stack), then "Freed by task <tf>/<pf>:" (with stack).

- Step 2 — Understand Pattern:
  - The three stacks are the "who read after free", "who allocated",
    "who freed". The "read after free" stack IS the bug site — its
    top-of-stack function holds a stale pointer.
  - Time delta: how much of the log elapsed between allocation, free,
    and access? Zero-time UAFs are usually double-frees or
    refcount-underflow; long-time UAFs are usually missed refcount
    increments.

- Step 3 — Correlated Pattern:
  - Look for `refcount_t` warnings (`refcount_t: <op> at zero`)
    earlier in the log — often the same object.
  - Check if the free stack is inside a workqueue callback (`kfree_rcu`
    is safe; naked `kfree` after `queue_work` is not).

- Step 4 — Form Hypothesis:
  - "Object at `<addr>` freed by `<free path>`; still referenced from
    `<data structure>` accessed by `<read path>` because
    `<removal from list / refcount drop>` was missed."

- Step 5 — Validate Hypothesis:
  - Grep the tree for all readers of the pointer field.
  - Suggest a fix: add `list_del_rcu` + `synchronize_rcu` before free,
    OR promote to `kfree_rcu`, OR fix the refcount get/put pair.

### Possibility 2: KASAN slab-out-of-bounds

- Step 1 — Issue Pattern:
  - `BUG: KASAN: slab-out-of-bounds in <fn>` + "The buggy address
    belongs to the object at <hex> which belongs to the cache
    <cachename> of size <sz>." + "The buggy address is located
    <off> bytes to the <right|left> of ...".

- Step 2 — Understand Pattern:
  - Off-by-one or wrong-size read/write past the allocation. Check
    `<off>` vs the struct size / array length.
  - "left" of the object → underflow (rare; usually a decrement bug).
  - "right" of the object → overflow (typical).

- Step 3 — Correlated Pattern:
  - If the cache is a generic `kmalloc-<n>`, the object type isn't
    known — need to trace from allocation site up in code.
  - If the cache is a named slab (`nfs_inode_cache`,
    `sock_inode_cache`), you know the struct type.

- Step 4 — Form Hypothesis:
  - "Access at `<obj>+<off>` reads past the end of `<struct>` because
    `<index>` came from `<user or attacker-influenced source>` without
    bound-check."

- Step 5 — Validate Hypothesis:
  - Read `<fn>` at the reported SHA; look for the array index /
    length variable computation. Propose either a bound check or a
    resize of the allocation.

### Possibility 3: lockdep circular dependency (ABBA)

- Step 1 — Issue Pattern:
  - `WARNING: possible circular locking dependency detected` + chain
    of `-> #0 (LockA){...}: <fn>`, `-> #1 (LockB){...}: <fn2>`,
    `-> #2 (LockA){...}`, closing the loop.

- Step 2 — Understand Pattern:
  - Two (or more) locks acquired in inconsistent order across code
    paths. Even if the actual deadlock never triggers (e.g. one path
    is rare), lockdep proves it's possible.
  - The `{<class>}` letters indicate acquire context (`R` read, `.`
    hardirq-safe, `-` softirq-safe, etc.). Mismatches signal
    IRQ-inversion sub-class of the same bug.

- Step 3 — Correlated Pattern:
  - Search for recent commits touching either lock's callers. New
    call chains commonly introduce the reversal.

- Step 4 — Form Hypothesis:
  - "Path `<X>` acquires LockA then LockB; path `<Y>` acquires
    LockB then LockA. Fix: pick one canonical order and refactor
    one of the paths."

- Step 5 — Validate Hypothesis:
  - Read both call chains at the reported SHA; identify the
    canonical order (usually the older / more heavily used chain).
  - Propose a diff moving the acquisition of one lock outside the
    other, or replacing one with a lock-free primitive.

### Possibility 4: kmemleak

- Step 1 — Issue Pattern:
  - `kmemleak: <N> new suspected memory leaks (see …/kmemleak)` +
    per-object block: `unreferenced object 0x<hex> (size <n>):` +
    allocation backtrace + `hex dump:`.

- Step 2 — Understand Pattern:
  - kmemleak scans for kmalloc pointers not referenced anywhere in
    kernel memory. False positives happen (pointer hidden in a xarray
    slot, pointer stored bit-inverted), but a consistent report over
    multiple scans is a real leak.
  - The allocation backtrace top frame is the leak site.

- Step 3 — Correlated Pattern:
  - Match against error paths in the allocating function — common
    pattern is "alloc, then early return without free".
  - Match against `.remove` / `.exit` paths — common pattern is a
    module unload leaving an allocation behind.

- Step 4 — Form Hypothesis:
  - "Function `<fn>` allocates `<obj>` at line `<n>`; error path at
    line `<m>` returns without `kfree(obj)`."

- Step 5 — Validate Hypothesis:
  - Read `<fn>` at the SHA. Propose either a `goto err_free` cleanup
    or a `devm_*` allocation switch if applicable.

### Possibility 5: OOM killer invoked

- Step 1 — Issue Pattern:
  - `<comm> invoked oom-killer: gfp_mask=<hex>, order=<n>, …` + `Mem-
    Info:` block + zone free / active / inactive counts + task list
    with oom_score + `Out of memory: Killed process <pid> (<comm>)`.

- Step 2 — Understand Pattern:
  - Two variants: (a) genuine memory pressure — total free + reclaim
    exhausted; (b) fragmentation — order-N alloc fails even though
    total free is high, look at the buddy allocator zone free per
    order.
  - Check `gfp_mask` for `__GFP_NORETRY` / `__GFP_NOWARN` — those are
    "opportunistic" allocs; failure is expected and OOM was invoked
    only if `__GFP_NOFAIL` or similar was mis-set.

- Step 3 — Correlated Pattern:
  - Which process was killed vs which invoked — a system daemon
    (dbus-daemon, systemd) invoking oom-killer often means a **kernel
    leak**, not a userspace bug.
  - Look for `slabinfo` in the log if `dump_stack` on OOM was
    enabled — a runaway slab is the tell for a kernel leak.

- Step 4 — Form Hypothesis:
  - Choose one of: (a) userspace bug — user needs to bound consumers;
    (b) kernel leak — track the leaking slab and route to Possibility
    4; (c) fragmentation — either raise `CONFIG_COMPACTION` or use
    `movable` zones.

- Step 5 — Validate Hypothesis:
  - Ask for `/proc/slabinfo` and `/proc/buddyinfo` snapshots taken
    just before the OOM if reproducible.

### Possibility 6: SLUB Redzone / Poison / Freepointer corruption

- Step 1 — Issue Pattern:
  - `SLUB: kmem_cache_<name>: Redzone overwritten` or
    `Poison overwritten` or `Freepointer corrupt`, plus an
    `Object dump:` with the mismatching byte offset.

- Step 2 — Understand Pattern:
  - `Redzone` = boundary sentinel — overwriting it = buffer overflow.
  - `Poison` = pattern (0x6b) placed on free — overwriting it =
    use-after-free.
  - `Freepointer corrupt` = free list pointer stomped — typically
    double-free or wild write.

- Step 3 — Correlated Pattern:
  - The offset within the object often maps to a specific struct
    field. Read the struct definition of the allocated type.

- Step 4 — Form Hypothesis:
  - Same as UAF/OOB in KASAN: identify the offending write site by
    caller of the cache's alloc/free.

- Step 5 — Validate Hypothesis:
  - Rebuild with `CONFIG_KASAN_GENERIC=y` (KASAN is strictly more
    precise than slab_debug) and reproduce; KASAN will name the
    exact function.

---

## 4 — Key Notes to Remember

- KASAN reports are self-contained blocks. Copy the block verbatim
  into the report evidence — do not paraphrase.
- The **first** report is the real one. Subsequent reports for the
  same address on different CPUs are races on the same corruption
  and should be listed as evidence, not as separate bugs.
- lockdep splats are proofs of a possible deadlock, not evidence that
  a deadlock happened. Treat them as bugs regardless — the reversal
  will eventually deadlock in production.
- `kmemleak` needs `kmemleak=on` on the cmdline plus
  `CONFIG_DEBUG_KMEMLEAK=y`; if the report is in-log it means both
  were on.
- On arm64 with MTE (`CONFIG_KASAN_HW_TAGS=y`), KASAN reports have
  slightly different formatting — the "Pointer tag: [0x<hex>]" line
  identifies the mismatched tag byte.
- If a KASAN report and a lockdep splat both appear, they may be
  the same bug (a UAF exposed while holding a lock). Analyse KASAN
  first — memory-safety fixes usually resolve the lockdep noise too.
- Never propose disabling the diagnostic (removing `CONFIG_KASAN=y`,
  suppressing lockdep via `lockdep_off`) as a fix — those are
  workarounds, not root-cause solutions.
