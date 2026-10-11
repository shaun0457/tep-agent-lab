# Reactor tag and limit table

## Basis

Values below use Downs and Vogel (1993), Tables 3, 4 and 6, Mode 1 base case.
Normal values are reference points, not universal setpoints or tolerance bands.
Normal operating constraints and shutdown constraints are separate. No additional
alarm setpoint is specified here. Unknown limits are not inferred.

## Tags and constraints

| Tag | Description | Unit | Normal value/range | Alarm/shutdown limits |
| --- | --- | --- | --- | --- |
| XMEAS(7) | Reactor pressure | kPa gauge | Base 2705.0; normal upper limit 2895 | Alarm unspecified; shutdown high 3000 |
| XMEAS(9) | Reactor process temperature | degC | Base 120.40; normal upper limit 150 | Alarm unspecified; shutdown high 175 |
| XMEAS(21) | Reactor cooling-water outlet temperature | degC | Base 94.599 | Alarm and shutdown unspecified |
| XMV(10) | Reactor cooling-water flow manipulation | % command | Base 41.106; command range 0-100 | Not a process alarm or shutdown limit |

## Flow units

The cooling-flow command percentage is not a flow measurement in m3/h.
The public base-case cooling-water flow is 93.37 m3/h (Table 1).

## Cross-document conflict

The normal upper temperature limit here is 150 degC. The troubleshooting note
deliberately states 145 degC for that same constraint. Preserve both source refs
for review; the manifest identifies the latter as an intentional fixture conflict.
