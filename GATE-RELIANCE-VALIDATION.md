# One-gate counterfactual query, November25

Base948058c5dd9edd2630ac29d5e971a2c5f3bab159. Existing-own feature, not a
new original tool or outside contribution. One selected gate becomes a merge;
all inputs, budgets, other nodes/gates stay fixed. No baseline pass required.
Unknowns are not zero/infinity, already failing checks are not newly lost passes.
No runtime enforcement, availability, observed freshness or bypass safety claim.

Original69tests retained. Added11tests with160seeded smallDAGs and independent
finite possibility-set enumeration for every selected gate and budget. Source
sets contain zero and hold additions include zero, so the oracle avoids empty
conditional gate output sets. This is a bounded mathematical check, not evidence
that real systems attain every bound. Includes multiple budgets, downstream gate
shielding, unknowns, mixed-input max semantics, disconnected invalid cycles,
full512node chain and exact1021*10^15 aggregate bound, permutation invariance,
CLI modes/status/error behavior and failed output.

80testsPASS actualCPython3.10.12,3.13.18,3.15.2 and neutral installedwheel3.15.2
outsidecheckout. Five mutants killed: retain cap, bypass all gates, label old
failures newly lost, mutate caller input, report baseline instead of candidate
status. Normal audit arithmetic/schemas unchanged. Source checkout only, no
package release. Hosted CI/published-tree verification pending review.
