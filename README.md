# agebudget

Check how declared source-age bounds accumulate through layered caches before wiring them together. Offline, standard-library Python, no service access.

A cache TTL measures permitted local reuse. It does not establish how old the source data already was when the cache filled. Two locally valid caches can return data older than either TTL.

This is an experimental engineering model, not a runtime guarantee. There are no observed users. It does not discover cache policies, inspect live responses or establish that data reflects the latest committed source state.

## An invented layered-cache example

```console
python -m pip install .
agebudget examples/layered.json
```

The example declares a source-age bound of 5 ticks, a Redis-like hold of 2 ticks delay plus 30 ticks residence, and a local hold of 1 tick delay plus 20 ticks residence. The returned-age upper bound is 58 ticks against a 40 tick budget. Exit 1 means the declared bound exceeds a budget or is unknown, not that an actual response violated it.

The explanation path is `source -> redis -> local`. These names describe a hypothetical architecture, not integrations with Redis or a browser.

```console
agebudget examples/gated.json
agebudget examples/unknown.json
```

The gated example places a declared age-rejection gate after the two holds. It caps permitted returned age at 40 ticks and exits 0. That gate may reject every value. It must check meaningful source age, reject older values on every path, and never serve stale fallback. Merely choosing a 40 tick local TTL is not such a gate.

The unknown example does not supply a source-age bound. Unknown propagates instead of silently becoming zero.

## Exact input contract

UTF-8 JSON, schema `agebudget-plan-v1`, with exactly three top-level keys:

- `schema`: the schema name.
- `nodes`:1 to 512 nodes with unique nonempty IDs of at most 128 characters.
- `budgets`:1 to 512 objects with `node` and integer `max_age`. Each target appears once.

All ages are **nonnegative integer ticks in the same caller-chosen unit**, not wall-clock timestamps. Each supplied scalar is at most 10^15. Bounds include their endpoints. No unit conversion or clock synchronization is inferred.

Every node has an `id`, a `kind`, and exactly the fields below:

| kind | additional fields | declared contract |
| --- | --- | --- |
| `source` | `max_age` | Bound on the age of source data when this node returns it. `null` means unknown. |
| `hold` | `inputs`, `max_delay`, `max_residence` | Uses all inputs; preserves their oldest source age and may add up to the declared delay and residence. Either scalar may be `null` for unknown. A local TTL restarted at fill is a residence term, not an age reset. |
| `merge` | `inputs` | Uses all inputs immediately, with no additional age. Output age is that of the oldest input. Not a race, fastest-replica selection or fallback. |
| `gate` | `inputs`, `max_age` | Uses all inputs, adds no age, and only returns values whose oldest source age is at most the cap. Rejection is allowed. Cap is required and cannot be `null`. |

Inputs are nonempty lists of distinct existing node IDs. Graphs must be acyclic, including disconnected portions, with at most 8192 edges. Duplicate JSON object keys, nonfinite numbers, floats, booleans used as numbers, extra fields and missing fields are errors. The CLI accepts at most 2 MiB per input, from regular files only. Directories,
FIFOs and device inputs are rejected before reading; symlinks to regular files
are allowed. A regular file on a slow filesystem can still block.

The bound calculation is deliberately simple:

- Source: supplied bound, or unknown.
- Merge: maximum incoming bound; unknown if any incoming bound is unknown.
- Hold: maximum incoming bound plus delay plus residence; unknown if any required term is unknown.
- Gate: minimum of incoming bound and declared cap; an unknown incoming bound becomes the cap, conditional on the gate contract.

No source age ever resets to zero merely because a hold loads data. A true source validation/recomputation belongs in a separately justified source contract; it is not inferred from an operation name.

## Reading reports

JSON schema `agebudget-report-v1` contains per-node bounds and budget checks. `upper_bound: null` is unknown, not infinity, observed freshness or a numerical zero. `unknown_at` names nodes missing required bounds. `conditional_on_gate` warns that some dependency path relies on a rejecting gate.

`explanation_path` follows a deterministically chosen oldest-bound dependency, or an unknown dependency. An active gate cap starts a new explanation path at that gate. It is a bound explanation, **not a concrete execution trace or proof the bound can be attained**. At a merge, other branches can also matter; the report does not enumerate all paths. Upstream shared dependencies, correlations and unrealizable simultaneous maxima can make these bounds conservative.

Check statuses:

- `BOUND_WITHIN_BUDGET`: the conditional declared upper bound is no larger than the budget. This is not proof of actual freshness or availability.
- `BOUND_EXCEEDS_BUDGET`: the bound is larger. An actual response need not be this old. Inspect the explanation and assumptions before changing a TTL.
- `UNKNOWN`: the supplied contract cannot establish a finite bound.

Overall status is `UNKNOWN` if any checked target is unknown, otherwise exceeds if any checked bound exceeds, otherwise within. Individual checks are retained, so an unknown does not hide another target's excess.

Exit 0: all checked bounds within budgets. Exit 1: completed audit with an unknown or excess. Exit 2: invalid input, read or output error. An output stream failure can leave partial JSON and is not a successful audit. Input validation errors produce no stdout report. The CLI does not create report files; redirect stdout if needed and check its exit status.
A verdict is returned only after a complete checked write and flush. Help and
usage messages use the same checked delivery path. Embedded `main(argv)` returns
a status without replacing or closing caller streams. Executable wrappers avoid
retrying failed buffered stdout at shutdown, preserving exit 2 rather than 120.
Diagnostics are best effort if stderr also fails. Partial output from an error
is not a usable report.

```python
from agebudget import audit, loads

with open('examples/layered.json', encoding='utf-8') as stream:
    report = audit(loads(stream.read()))
```

`audit` does not mutate inputs. `loads` supplies strict JSON parsing. The CLI, unlike this small API example, enforces the byte limit while reading.

## Compare a proposed model change

```console
agebudget before.json --compare-to after.json
```

Both inputs must be valid plans with **identical budget targets and limits**.
The caller must keep tick units and the meaning of shared node IDs the same;
the file format cannot verify those assumptions. A changed budget is rejected,
so loosening a target cannot silently look like a configuration improvement.

For example, copy `examples/layered.json` to `before.json` and `after.json`, then
change only the local hold's `max_residence` from 20 to 2 in `after.json`.
Its declared bound falls from 58 to 40 ticks, with `bound_delta: -18`, and the
unchanged 40-tick budget changes from `BOUND_EXCEEDS_BUDGET` to
`BOUND_WITHIN_BUDGET`. This does not establish that the shorter residence is
operationally safe or changes any observed response age.

The `agebudget-comparison-v1` report includes all node IDs in sorted order,
with full before/after audit rows. Added and removed nodes have one side set
to `null`. `BOUND_LOWER`, `BOUND_HIGHER` and `BOUND_UNCHANGED` compare finite
bounds, with signed after-minus-before `bound_delta`. `BOUND_ESTABLISHED`,
`BOUND_LOST` and `STILL_UNKNOWN` retain unknowns without treating them as
infinity or zero; their delta is `null`. Inspect the full rows for changed
node kinds, explanation paths, unknown causes and gate conditions even when
the bound is unchanged. Checks retain both statuses and bound excesses.

The top-level `status` and exit 0/1 describe the **candidate's budget audit**,
not whether it is better than the baseline. Exit 2 means either input,
comparison compatibility, reading or output failed. Each input has its own
2 MiB limit. Both are validated before stdout receives a report.

```python
from agebudget import compare
report = compare(before_plan, after_plan)
```

Comparison does not prove availability, rollout safety, model equivalence,
actual freshness or a concrete execution trace. A lower bound may come from
stronger assumptions or a gate that rejects every value, not a runtime benefit.

## When not to use it

No HTTP cache implementation or RFC 9111compliance claim. No header parsing, `Age`/`Date` correction, stale-while-revalidate, stale-if-error, validators, refresh scheduling, queues, cache-key isolation, invalidation correctness, clock uncertainty, write visibility, probabilistic freshness or service availability model. A declared gate requiring unavailable provenance is an assumption, not a fix. Unbounded outage fallback should be represented as unknown, not a convenient finite TTL.

The tool answers an age-bound question under supplied contracts. It does not decide whether data is semantically up to date, whether a TTL reduction is safe, or whether a system will return anything.

## Existing work and why this is separate

This is established bound propagation, not a new algorithm or first freshness tool. The contribution is a small strict offline contract, machine-readable unknowns, gate caveats and deterministic explanations.

- [RFC 9111](https://www.rfc-editor.org/rfc/rfc9111.html) describes HTTP age across caches. This tool does not implement HTTP.
- [Ageglass](https://github.com/nicholas-ruest/ageglass) simulates periodic multi-rate pipelines and their lineage age. agebudget instead evaluates declared upper bounds without a schedule or simulation horizon.
- [CacheWitness](https://github.com/godz1llla/cachewitness) tests cross-user contamination using HTTP experiments. It addresses a different cache correctness question.
- [cache-analyser](https://github.com/alan-ws/cache-analyser) and [CacheLint](https://github.com/suhteevah/cachelint) inspect caching patterns rather than this input contract.
- [HTTP Cache Freshness Calculator](https://0xelitesystem.github.io/http-cache-freshness-calculator/) evaluates HTTP response age inputs.

If one of those workflows matches the real question, use it instead. This project has not been independently benchmarked against those tools.

## Development

```console
python -m unittest discover -s tests -v
```

Tests include finite possibility-set enumeration over synthetic DAGs, independently compared with bound propagation. These are mathematical fixtures, not production validation.

MIT license. No telemetry or network operations.
