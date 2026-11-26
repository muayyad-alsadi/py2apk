# Archived Goals

## How to view archived goals

To view an archived goal named `my-goal-slug` at a given git hex like `abc123`
You can use the following commands:

```bash
# list files
git ls-tree -r --name-only abc123 -- "goals/my-goal-slug/"
# show an entire file (an agent on limited token budget should avoid it)
git show abc:goals/my-goal-slug/plan.md
# show important sections with line numbers
git show 8eabd0c:goals/my-goal-slug/plan.md | egrep -n '^(#|-)'
# show a section from line line_from to line line_to and quit at line_end=line_to+1
git show 8eabd0c:goals/my-goal-slug/plan.md | sed -n '<line_from>,<line_to>p; <line_end>q' 
```

## How to archive

The goal description can be read from the first line without the `# Goal:` prefix

```bash
desc=`cat ./goals/my-goal-slug/goal.md  | head -n 1 | cut -d ':' -f '2-'`
```

```
* `<my-goal-slug>`, 📥 <YYYY-MM-DD> `<commit>`, ✅ <YYYY-MM-DD> `<commit>`, 📦 <YYYY-MM-DD> `<commit>`
  * <Description>
```

* 📥 is when the plan introduced
* ✅ is when the plan was implemented and verified
* 📦 is just before archiving the plan, plan exists at this point. after that it might not.


## List of Archived Goals

* `is-comparison`, 📥 2026-11-25 `dbbcb88`, ✅ 2026-11-25 `e877355`, 📦 2026-11-25 `eebc8e9`
  * Compare with is and is not
* `except-tuples`, 📥 2026-11-25 `e877355`, ✅ 2026-11-25 `ae407e7`, 📦 2026-11-25 `eebc8e9`
  * Catch more than one exception type
* `hashset-literals`, 📥 2026-11-25 `e877355`, ✅ 2026-11-25 `4815f8b`, 📦 2026-11-26 `549add6`
  * Lower set literals to HashSet
* `keyword-arguments`, 📥 2026-11-25 `e877355`, ✅ 2026-11-25 `eebc8e9`, 📦 2026-11-25 `eebc8e9`
  * Allow keyword arguments on calls
* `minsdk-api-check`, 📥 2026-11-25 `e877355`, ✅ 2026-11-25 `8eabd0c`, 📦 2026-11-26 `549add6`
  * Reject APIs newer than minSdkVersion
* `comparison-values`, 📥 2026-11-25 `0d0d9b1`, ✅ 2026-11-25 `e18f983`, 📦 2026-11-25 `eebc8e9`
  * Store is and is not
* `compiler-package`, 📥 2026-11-25 `fb9de0f`, ✅ 2026-11-25 `867d226`, 📦 2026-11-25 `eebc8e9`
  * Split the Jasmin compiler into a package
* `comparison-expressions`, 📥 2026-11-25 `b555190`, ✅ 2026-11-25 `b05a695`, 📦 2026-11-26 `549add6`
  * Store comparisons and boolean tests
* `true-division`, 📥 2026-11-25 `702f2c8`, ✅ 2026-11-25 `b04dc94`, 📦 2026-11-26 `549add6`
  * Compile true division and float arithmetic
* `slice-values`, 📥 2026-11-25 `2be3b90`, ✅ 2026-11-25 `0aa3f09`, 📦 2026-11-26 `549add6`
  * Use a slice as a value
* `tuple-for`, 📥 2026-11-25 `b815168`, ✅ 2026-11-25 `b94758f`, 📦 2026-11-26 `549add6`
  * Unpack a pair in for
* `math-time`, 📥 2026-11-25 `4874d99`, ✅ 2026-11-25 `bb91a35`, 📦 2026-11-26 `549add6`
  * Lower math and time
* `string-split-join`, 📥 2026-11-25 `6c3bd2d`, ✅ 2026-11-25 `cac0495`, 📦 2026-11-26 `549add6`
  * Lower literal split and join
* `function-values`, 📥 2026-11-26 `937ff84`, ✅ 2026-11-26 `64c065d`, 📦 2026-11-26 `549add6`
  * Use a function as a value
* `higher-order-builtins`, 📥 2026-11-26 `cdd0393`, ✅ 2026-11-26 `549add6`, 📦 2026-11-26 `549add6`
  * Lower map, filter, and sorted with key
