---
name: civil-engineering
description: Civil engineering fundamentals including structural analysis, geotechnics, hydraulics, transportation, and construction management
license: MIT
compatibility: opencode
metadata:
  audience: engineers
  category: engineering
---

<!-- Vendored for ArchPilot reference (2026-10-14) from
https://github.com/ffsshhttiikk/opencode-agents-skills (engineering/civil-engineering, MIT).
Guidance only — illustrative snippets; ArchPilot never executes skill code.
Caveat: the reinforced_concrete_column example references an undefined `Ast`; treat all
formulas as preliminary and verify against current codes before use. -->

## What I do
- Analyze structural systems and load paths
- Design foundations and earth retention systems
- Calculate hydraulic and drainage requirements
- Design transportation infrastructure
- Specify construction materials and methods
- Perform geotechnical site investigations
- Create construction documents and specifications

## When to use me
When designing building structures, foundations, drainage systems, roadways, or any civil
infrastructure project requiring structural analysis — and when wording structural-safety
warnings, assumption callouts, and engineering limitations for a design/estimating tool.

## Core Concepts
- Structural analysis and load combinations
- Foundation design (shallow and deep)
- Concrete and steel design
- Geotechnical engineering principles
- Hydrology and hydraulic design
- Transportation engineering
- Construction materials and methods
- Building codes and regulations
- Site development and grading
- Environmental considerations

## Code Examples

### Load combinations (ASCE 7)

```python
def asce7_load_combinations(dead, live, snow, wind, seismic):
    return [
        1.4 * dead,
        1.2 * dead + 1.6 * live,
        1.2 * dead + 0.5 * snow + 1.6 * live,
        1.2 * dead + 1.6 * snow,
        0.9 * dead + 1.0 * wind,
        1.2 * dead + 1.0 * seismic + live,
    ]
```

### Allowable bearing capacity (Terzaghi) — preliminary

```python
import math
def allowable_bearing_capacity(phi_deg, c, gamma, B, Df, FS=3.0):
    phi = math.radians(phi_deg)
    Nq = math.exp(math.pi * math.tan(phi)) * math.tan(math.radians(45 + phi_deg / 2)) ** 2
    Nc = (Nq - 1) / math.tan(phi)
    Ngamma = 2 * (Nq + 1) * math.tan(phi)
    qu = c * Nc + gamma * Df * Nq + 0.5 * gamma * B * Ngamma
    return qu / FS
```

### Concrete mix design (ACI 211.1) — preliminary

```python
def aci_concrete_mix(fck, slump, max_aggregate, CA_ratio):
    water = 300 + slump * 10
    if fck > 4000:
        water -= (fck - 4000) / 200 * 5
    air = 1.5 if max_aggregate < 1.5 else 1.0
    wcmax = 0.45 if fck > 5000 else 0.50
    cement = water / wcmax
    coarse = 0.65 * 165 * 62.4 / (1 + CA_ratio * 2.65)
    fine = 165 - water / 62.4 - cement / 94 - coarse / 62.4
    return {"cement": cement, "water": water, "coarse": coarse, "fine": fine,
            "air": air, "w_c": water / cement}
```

### Hydrology (Rational Method, Manning)

```python
def rational_method(C, i, A):
    return C * i * A
def storm_drain_design(Q, A, n, S):
    V = Q / A
    D = ((Q * n) / (0.463 * S ** 0.5)) ** 0.375 * 12
    return {"velocity": V, "diameter_in": D}
```

## Best Practices
- Apply factors of safety based on consequence of failure
- Follow applicable codes (IBC, ASCE 7, ACI, AISC, EC, local)
- Perform site investigation before foundation design
- Consider constructability
- Account for settlement
- Design for durability (freeze-thaw exposure)
- Document all assumptions and design criteria
- Peer-review critical structural elements

## ArchPilot usage notes
- Use for wording structural-safety warnings and assumption callouts, never as verified
  engineering output.
- ArchPilot estimates are preliminary and require professional review (PRELIMINARY_STAMP).
- SI units preferred in-app; convert carefully when referencing imperial examples.
