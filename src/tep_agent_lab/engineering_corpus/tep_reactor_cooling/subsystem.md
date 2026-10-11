# Reactor and cooling-water loop

## Scope and source

Original teaching material based on Downs and Vogel (1993), process description
and Tables 1, 3, 4 and 6. This corpus covers the reactor and its internal cooling
bundle. It is engineering source content for compiler review, not an operating
authorization or a reviewed engineering-context revision.

## Equipment and heat removal

Gas feeds react to form liquid products; the liquid retains the catalyst.
The reactions release heat. The internal cooling bundle removes reaction heat.

## Measurement identity

XMEAS(21) measures reactor cooling-water outlet temperature, in degC.
XMEAS(9) measures reactor process temperature; these are different quantities.

## Control relation

XMV(10) manipulates reactor cooling-water flow. Increasing cooling flow is a
means of increasing heat removal, subject to the thermal state and available
utility conditions. This qualitative relation specifies no controller tuning.
