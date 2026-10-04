# Will There Be Overtime — bounded score-contract adapter

Candidate code; live capability requires root integration and publication verification.

The offer keeps its exact Stake market/outcome ID, Yes/No label, price, group and condition. Yes prices and settles as regulation `three_way:draw`; No as regulation `double_chance:home_away`. Their canonical immutable selection IDs and allocation families remain those existing score contracts, preventing additional issuance of the same payoff. Both validated families must be present at discovery; existing scope, model promotion, feature, provenance, numerical reproduction and funding guards still run. No model report is rewritten and no new validation approval is inferred.

The two sides are devigged as one exclusive binary condition, independently of any concurrently offered 1x2 or Double Chance condition. All complete conditions remain available for anchoring and guard review. After those checks, duplicate canonical IDs involving an overtime offer retain the highest current guarded decimal odds, with deterministic tie breaks, before allocation/publication. Original held receipts are untouched.

Rule evidence checked 2026-10-04:

- [Stake Basketball rules](https://stake.com/policies/sportsbook): regulation draw determines Yes even if overtime is not played. NBA/WNBA parsing is eligible; WNBA still requires both validated model families and currently lacks them.
- [Stake American Football rules](https://stake.com/policies/sportsbook): settlement uses official association statistics. [Official NFL 2026 Rule 16-1-1](https://static.www.nfl.com/image/upload/fl_attachment/league/tqivdkzt9mu6wdgsh1ku.pdf), printed page 65/PDF page 72, requires overtime for tied regulation scores in regular and postseason games. This establishes the payoff identity for completed normal games, not preseason/exhibitions.

NFL discovery requires phase evidence copied from the existing nflverse schedule adapter (REG/WC/DIV/CON/SB), matching the game ID, source and declared phase. EvidenceGuard additionally rehashes the immutable CSV response and reproduces phase, both teams, season and exact start. A default `Game.game_type=regular` is insufficient. Old cached games without phase evidence remain unsupported until an ordinary source refresh supplies it. NFL settlement requires a final normal game plus verified completed regulation scores; missing or incomplete evidence remains pending.

No NHL, MLB, NCAA, preseason or unsupported-title inference is added. No production database or model artifact is changed by this candidate.
