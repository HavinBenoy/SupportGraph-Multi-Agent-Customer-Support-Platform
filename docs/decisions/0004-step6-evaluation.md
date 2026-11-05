# 0004: Evaluation design (build step 6)

Status: accepted. Date: 2026-11-04.

1. **Score outcomes, not intentions.** One "handled correctly" definition per ticket kind is applied identically to the support graph and
   the step 2 baseline, so the comparison is like for like and the baseline's inability to act or escalate shows up as what it is.
2. **A held-out set, reported before tuning.** The dev set was in view when the offline fakes were written, so it overstates quality. The
   held-out set exists to show the gap; its first measurement is recorded in docs/STEP6.md. Tuning against it retires it (write a v3).
3. **Safety is a gate, quality is a trend.** Leaks, unauthorized or unexpected tool calls, writes without a decision, crashes and any failed
   security case fail the run outright. Quality metrics fail only on regression against a stored reference beyond a tolerance.
4. **A judge is untrusted until calibrated.** Judge scores are reported with their agreement with human labels (kappa, missed hallucinations);
   an axis without enough negative examples is labelled "not trusted". The shipped labels are model-drafted and flagged for human review.
5. **Model-independent floors for costly misses.** Where a miss is expensive and a rule is cheap (safety, legal threats, duplicate
   charges), code adds an escalation regardless of what the triage model says. It can only add, never remove; its precision cost is
   measured. This is the one routing behaviour change in step 6.
6. **Do not tune the fakes to the held-out set.** Their gaps are the baseline a real model must beat.
7. **Evaluations are side-effect-free by construction**: own ticket ids, no approvals, exact-row cleanup, and a throwaway-database recommendation for the cross-tenant fixture.
