# Zero-Evaporation Cooling Specification — Monroe Campus

**Directive 35 | June 2026 | For June 8 Whitepaper**

## Executive Summary

The Monroe Campus will employ a **zero-evaporation, closed-loop thermal management system** consuming zero potable water for cooling under normal operation. This eliminates the primary water consumption vector associated with conventional data centers and neutralizes community concerns about aquifer depletion in Union County.

## Cooling Architecture

### Primary: Direct-to-Chip Liquid Cooling (DCLC)

| Parameter | Specification |
|-----------|--------------|
| Coolant | Dielectric fluorinated fluid (3M Novec 7200 or equivalent) |
| Loop type | Closed-loop, zero evaporation |
| Supply temperature | 45–50°C (warm-water cooling) |
| Heat rejection | Dry cooler / radiator array |
| Water consumption | 0 gallons/MWh (closed loop) |
| PUE contribution | ≤ 1.04 |
| Vendor | CoolIT Systems / ZutaCore / equivalent |

**How it works:**
- Coolant circulates directly to CPU/GPU cold plates via microchannel heat sinks
- Heated coolant returns to a dry cooler array for passive ambient heat rejection
- No evaporative step — heat is rejected sensibly to air
- System is fully sealed; make-up fluid required only for maintenance intervals (~5% annually)

### Secondary: Immersion Backup (Optional Phase 2)

- Single-phase immersion for high-density GPU racks
- Same dielectric coolant, same dry cooler loop
- Zero evaporative loss; same closed-loop architecture

### Tertiary: Adiabatile Backup (< 40 hours/year)

For extreme ambient temperature events (> 110°F):
- Small adiabatic assist (evaporative pre-cooling of intake air) — **BUT only** using harvested rainwater or municipally-supplied non-potable graywater
- Annual water budget: **< 50,000 gallons** (equivalent to ~4 US households)
- This is strictly a backup; normal operation requires zero water.

## Water Budget Comparison

| Cooling Method | Water Consumption (gal/MWh) | Annual for 300MW (gallons) |
|---------------|---------------------------|---------------------------|
| Evaporative cooling tower | 400–700 | 1.05–1.84 billion |
| Air-cooled chiller | 0 (dry) | 0 |
| **This spec (DCLC + dry cooler)** | **0 (normal)** | **0** |
| Adiabatic backup (extreme heat) | variable | < 50,000 |

## Aquifer Impact Analysis

- Union County sole-source aquifer: **Cretaceous Sand Aquifer**
- Baseline annual recharge: ~12 billion gallons (within Monroe region)
- Proposed campus water draw: **ZERO for cooling**
- Domestic/irrigation use (on-site staff + landscaping): **~2 million gal/year** (0.017% of recharge)
- Domestic water sourced from municipal supply, not direct well extraction

**Conclusion:** The Monroe Campus will be a net-zero water withdrawal facility with respect to the local aquifer. No groundwater permitting beyond standard municipal connection is required.

## Supporting Infrastructure

- **Rainwater harvesting**: 5 million gallon cistern capacity (roof + parking area capture)
- **Graywater recycling**: On-site treatment for landscape irrigation
- **Condensate recovery**: HVAC condensate collected (estimated 500k gal/year at 300MW)
- **Zero Liquid Discharge (ZLD)**: All process wastewater treated and reused on-site

## Regulatory Compliance

| Standard | Compliance |
|----------|-----------|
| NC DEQ Groundwater Permit | Not required (no withdrawal) |
| Union County Water Allocation | Zero allocation requested |
| EPA Clean Water Act §402 NPDES | Zero process discharge |
| Local aquifer protection ordinance | Voluntary compliance + 2x monitoring |

## Certification Targets

| Certification | Target |
|--------------|--------|
| Water Use Intensity (WUE) | 0.0 L/kWh (operational) |
| LEED v5 Water Efficiency | Prerequisite + all available credits |
| ENERGY STAR Data Center | Top 25th percentile |

---

*This specification is a living document. Final equipment selection subject to detailed engineering and vendor procurement cycles. The underlying principle — zero evaporative water consumption — is a binding design constraint, not a target.*
