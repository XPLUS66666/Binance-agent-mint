# Agent drop patrol and tracking

## Known openings: prewarm, not a last-minute scheduled task

- A known opening time is not covered by a cron that fires minutes before the opening, and it is not covered by a 5-minute narrow watch. Digital Bomb 2026-11-04 proved this again: a runonce set for 14:29 did not start its script until 14:30:26, and the first simulation at 14:30:41 was already sold out.
- For a known opening, start a resident loop at least one hour before it, finish prewarm (contract, mint function and calldata, eligibility, gas, RPC failover, both wallet slots) and report prewarm completion to the user before the opening. Poll lean, every 2-5 seconds, with only the single essential opening-state or simulation call, and fire both wallets in parallel at the opening.
- If prewarm has not completed and been reported, record the window as not prewarmed. Never describe a scheduled task as preparation.

## Check the registration and voucher window on discovery

- The on-chain open time is not the real deadline when a mint is voucher-gated. AGI 2026-11-05 proved it: the patrol reported the @AgentIdETH name at 16:41 but only the name, without a same-round deep dive into registration, allocation and voucher status. The 500 public free shares were pre-claimed through backend vouchers before the 23:00 on-chain open, so by the time a voucher was requested the round was already full, and one wallet was not even registered (whitelist registration had closed).
- On the same round a voucher-gated project is discovered, check its registration, allocation and voucher status (alloc endpoint, trial voucher request, registration open or closed) and record the registration window as the real deadline, not the on-chain open time. A backend registration or voucher window usually closes before the contract opens.
- Queue voucher-gated free projects separately and report them first, because their voucher window closes earlier than their open time.
- Add a leg for new Agent ID mint sites (new domain, new official account, voucher-gated mint) so a new mint platform is caught at registration time, not at open time.
- When a free round is voucher-gated, report the real remaining free allocation from the backend (vouchers issued or allocation), not just the on-chain supply, and say plainly when the backend round is already full.

## Discover beyond one source and verify on chain

- Search platform listings in addition to social timelines, because a timeline keyword filter cannot see listings that never mention the filter terms.

- Check the timelines of named project accounts in addition to keyword search, because a small account can publish a live claim without trending keywords.

- Keep the keyword set broad enough to cover agent wallet claims in general, and add a project name to the set as soon as an account search finds it.

- Record the source of each candidate, so a later check can tell whether it came from a listing, a timeline, or a keyword hit.

- Read the contract before you describe a candidate as free, and check the mint price, the mint state, the supply, and the claimed name against the code.

- Read what each mint argument means before you call a price final, because a nonzero price field can belong to a paid tier while a different argument gives the free claim.

- Report a paid mint or a paid curve as a separate paid item, and never present it as a free claim.

- Surface owner powers that can change the price, pause minting, or withdraw funds, so the user can judge risk before acting.

## Treat a broken leg as partial and find a second route

- Treat any broken discovery leg as partial coverage for that round.

- Label the round partial in the round record when a leg is stale, blocked, rate limited, or unscanned.

- Never report a partial round as a clean result with no new project.

- Record which leg broke, what it last showed, and what time that reading came from.

- Read a stale catalogue through the browser, web search, and GitHub instead of repeating the stale read.

- Check a blocked launchpad through its official social account and third party aggregation instead of stopping at the block.

- Scan an unscanned chain through a different RPC, a block explorer new contract list, and known launchpad new listings instead of leaving the chain uncovered.

- Work around a rate limited social leg through an account timeline mirror, web search, and the official account timeline instead of filing the leg as done.

- Search the official site and domain for a name-only candidate in the same round instead of filing the name and waiting.

- Recheck any contract you find on chain in that same round before you describe the candidate.

- Push a name-only candidate or a small free quota only once as a labelled first report that is not yet fully verified, and state the verified parts, the unverified parts, and the next route.

- Never mint, sign, or connect on the basis of a first report, and file the candidate as unresolved with its next route named when no contract is found.

- Write down each second route you tried and whether it worked, even when it found nothing new.

## Track every open project to one close

- Create one tracking record for each pushed project, and recheck that record on every patrol round.

- Record the fixed tier cap, the current minted count, the threshold that changes the price or opens the pool, the price that applies after the threshold, the pool open flag, and the paused flag when you file a project.

- State the on chain source for the cap and the minted count in the record so a later round can recheck the same fields.

- Keep the incremental scan and the full window rescan as separate checks so a project missed by one check can still be caught by the other.

- Push one progress-only update when an open project first crosses 80 percent of its cap or fixed tier, and do not repeat that progress notice for the same crossing.

- Escalate a free claim at 95 percent with the verified price, the minted share, and the remaining supply, so the user can decide without another lookup.

- Report a threshold crossed between patrol rounds in that same round with the new minted count and the new price instead of filing it as routine.

- Do not auto buy a paid tier on the basis of this record and leave the buy decision to the user.

- Send one closing update when a project sells out, closes its window, stops minting, or crosses its final threshold, and then mark the record closed.

- Do not push the same project again unless a material change reopens it, and state that change in the new update.

## Check a held item sell path and value without selling

- Read the contract ABI from a verified source before you report that no sell path exists.

- Try Sourcify or another verified ABI source when the first ABI fetch returns a 404 or an empty result.

- Treat a failed fetch as an unknown result and keep checking other sources before you close the item.

- Read the market contract functions for listing, buying, cancelling and fees when the ABI is available.

- Check Element and OKX in read only mode for floor price, listing count and recent trades before you report value.

- Record listings and trades separately from floor price so a single listing does not become the floor.

- Report an item as unlisted when neither market shows a listing and say that no listing was found.

- Read the contract mint price and the market floor price as two different numbers, and never report the mint price as the floor price when the market shows no floor.

- State the supply minted and the total supply beside the floor when both are known.

- Compare at least two market sources when they disagree and report the disagreement before you act.

- State the sources you tried and the exact failure when a floor or a sell path stays unknown, and keep the item in the next round instead of closing it as unsellable.

- Do not place a listing, move the item or start a sale while you are only checking value, and send a sale only after a verified path and a verified price are both present.

## Diagnose a blocked mint before blaming eligibility

- Treat a generic simulation block on an unverified contract as an unknown block, not as proof that the user is ineligible.

- Recheck the chain quote, the free quota, the price, and the eligibility fields when a block occurs, and check whether a project side balance that pays an auxiliary mint cost has fallen below the per mint fee.

- Retry the simulation from fresh state after that balance recovers instead of concluding the block is permanent.

- Record which hypothesis each failed attempt disproved, and stop testing when the next test would use no new source evidence.

- Report what is known, what is unknown, and what source would settle the block before asking for another signature or transaction.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Use the installed Binance Agentic Wallet skill for standard wallet reads, and keep claim verification on chain rather than on a project page.

- Use the installed X read-only scanning skill for the social leg, and keep the platform listing leg separate from it.

- Ask the user before acting on a candidate whose verification is incomplete, rather than pushing a partial claim as a verified one.
