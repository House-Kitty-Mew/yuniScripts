# SIMULATED_SOCIAL — Social Simulation Engine

## Overview
Adds complete social simulation on top of SIMULATED_PEOPLE + SIMULATED_RELATIONSHIPS.
Personas now have boredom, social exhaustion, three-tiered memory, and
deep relationship tracking.  Crisis mechanics handle suicide risk when
social needs are catastrophically unmet.

---

## 1. BOREDOM SYSTEM (0-100)

| Level | Effect |
|-------|--------|
| 100-75 | Content |
| 75-50 | Mild restlessness |
| 50-25 | Bored — seeks activities |
| 25-10 | Agitated — social is primary need |
| 10-1 | **Critical — overrides food/water** |
| 0 | **Crisis — suicide check** |

Decay varies by archetype (vagabond 2.0x, farmer 0.5x).

## 2. SOCIAL ACTIVITIES (12 types)
Conversation, Playing, Hanging Out, Eating Together, Drinking,
Dancing, Storytelling, Gift Giving, Celebration, Arguing,
Resting Alone, Resting in Company.

Each has boredom recovery, exhaustion cost, skill effects, relationship delta.

## 3. SOCIAL EXHAUSTION (0-100)
Gain from activities, decay from rest.  At 100: burnout (3-tick freeze).

## 4. RESTING
Short sleep (3 ticks), Full sleep (8 ticks), Rest alone/with company.
Company improves recovery.

## 5. RELATIONSHIP DEPTH
Acquaintance -> Friend -> Close Friend -> Best Friend ->
Romantic Interest -> Partner -> Married.
Rival/Enemy tracks for negative relationships.
Time decay: -0.5/tick with distance.

## 6. CRISIS MANAGEMENT
At boredom = 0: suicide check if next action isn't social.
Base 1% + 0.10%/tick + behavior modifier - 2%/close friend.
On success -> death.  On failure -> forced social.

## 7. THREE-TIER MEMORY
| Type | Max | Prune | Detail |
|------|-----|-------|--------|
| Short-term | 50 | FIFO | Context clues |
| Medium-term | 100 | Low importance + old | Goals, plans |
| Permanent | 20 | Never | Trauma, triumphs |

## 8. INTEGRATION
- Hooks into on_simulation_cycle_end
- Extends ext_sp_profiles and ext_rel_relationships
- All 8 archetypes supported with modifiers
