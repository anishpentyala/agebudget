# Checked stream delivery, November25

Base97ead292e8a14abe9efc6162346d401278020bc9. One existing-own reliability fix,
not a new original tool. No changes to age arithmetic, input contract, report
schemas, comparison or headroom calculations.

Fresh baseline had62passing tests. Added7test methods with multiple stream/mode
subcases. Final new tests on unchanged production code: fail, with unavailable
stdout AttributeError, equal-length float/integer-subclass results accepted,
unchecked final stderr failure and real usage stderr /dev/full returning120.
The streams are explicit synthetic failure injection except Linux /dev/full;
no ordinary-file production incident is asserted.

Candidate69testsPASS on actualCPython3.10.12,3.13.18,3.15.2. Neutral installed wheel
outside checkout69PASS on3.15.2, including installed console /dev/full paths.
Original62retained. Four mutations were killed: remove type check, remove
unavailable-stream check, check stdout only, remove final flush.

Embedded main retains caller stream identity and ownership. Process wrapper
checks both streams and replaces only a failed stream with None, preventing a
shutdown retry; both report and help/usage share checked immediate delivery.
Not a durability guarantee or evidence that a downstream consumer processed text.
No Windows/macOS tests. Hosted CI and published-tree checks are still pending.
