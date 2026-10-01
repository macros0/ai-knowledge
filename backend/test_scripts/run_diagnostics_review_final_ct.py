"""One frozen final pass after the bounded review fixes; never overlap stages."""
import run_diagnostics_final_ct as queue
import run_diagnostics_targeted_ct as targeted
import run_diagnostics_remaining_ct as remaining
import run_diagnostics_live_ct as live


if __name__ == "__main__":
    for block in (targeted, remaining, live):
        queue.history = []
        block.main()
