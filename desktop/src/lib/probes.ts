// Canonical signal ids (ProcessGraph semantic ids) used by the development probes.
// Runtime ids such as XMEAS(9) are display labels only (`source_locator`), never keys.
// tests/test_application_views.py checks these constants against the pinned ProcessGraph.
export const TEMPERATURE_SIGNAL = "reactor.temperature_measurement"; // XMEAS(9)
export const PRESSURE_SIGNAL = "reactor.pressure_measurement"; // XMEAS(7)
export const PROBE_ENTITY = "reactor";
export const HISTORY_POINTS = 30;
