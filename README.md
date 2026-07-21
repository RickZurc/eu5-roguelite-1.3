# Roguelike Universalis

A roguelite layer for Europa Universalis V (1.3.x). Every advance you research strips its normal effect and instead opens an **upgrade selection**: pick one of three random buffs/unlocks.

## Features
- **Rolls** from researching advances and from entering a new age (guaranteed legendary).
- **Rarity tiers** — Common / Rare (2×) / Legendary (5×).
- **Cursed rolls** — rare/legendary picks can be stronger but inflict a lasting drawback.
- **Reroll & Banish** — spend banked tokens (earned by winning great battles) to redraw or permanently remove an upgrade from your pool.
- **Synergy sets** — upgrades are tagged Military / Naval / Economic / Administrative; collecting 4 / 8 / 12 of a theme grants escalating set bonuses.
- **Escalating threat** — the more upgrades you take, the more the world distrusts your rising power.
- **Status window** — an action-bar button shows your tokens, notoriety and synergy progress.

Everything is configurable in the **Community Mod Menu** (requires the Community Mod Framework).

## Regenerating for a new patch
The mod content is generated from the current vanilla advances by `scripts/advance_parser.py`. See the script header for usage.
