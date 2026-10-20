# General Modeling Network Specification (GMNS)

**Name:** `gmns`
**Version:** `0.96`
**Homepage:** <https://github.com/zephyr-data-specs/GMNS>

The General Modeling Network Specification (GMNS) defines a common machine (and human) readable format for sharing routable road network files. It is designed to be used in multi-modal static and dynamic transportation planning and operations models.

## Resources

| Resource | Required | Path | Description |
| --- | --- | --- | --- |
| [`link`](#link) | yes | `link.csv` | A link is an edge in a network, defined by the nodes it travels from and to.… |
| [`node`](#node) | yes | `node.csv` | A list of vertices that locate points on a map.… |
| [`geometry`](#geometry) |  | `geometry.csv` | The geometry is an optional file that contains geometry information (shapepoints) for a line object.… |
| [`lane`](#lane) |  | `lane.csv` | The lane file allocates portions of the physical right-of-way that might be used for travel.… |
| [`link_tod`](#link_tod) |  | `link_tod.csv` | Handles day-of-week and time-of-day restrictions on links |
| [`location`](#location) |  | `location.csv` | A location is a vertex that is associated with a specific location along a link.… |
| [`movement`](#movement) |  | `movement.csv` | Describes how inbound and outbound links connect at an intersection. |
| [`movement_tod`](#movement_tod) |  | `movement_tod.csv` | Handles day-of-week and time-of-day restrictions on movements. |
| [`use_definition`](#use_definition) |  | `use_definition.csv` | The Use_Definition file defines the characteristics of each vehicle type or non-travel purpose (e.g., a shoulder or parking lane).… |
| [`use_group`](#use_group) |  | `use_group.csv` | Defines groupings of uses, to reduce the size of the allowed_uses lists in the other tables. |
| [`time_set_definitions`](#time_set_definitions) |  | `time_set_definitions.csv` | The time_set_definitions file is an optional representation of time-of-day and day-of-week sets to enable time restrictions through `_tod` files. |
| [`segment`](#segment) |  | `segment.csv` | A portion of a link defined by `link_id`,`ref_node_id`, `start_lr`, and `end_lr`.… |
| [`segment_lane`](#segment_lane) |  | `segment_lane.csv` | Defines added and dropped lanes, and changes to lane parameters.… |
| [`signal_controller`](#signal_controller) |  | `signal_controller.csv` | The signal controller is associated with an intersection or a cluster of intersections. |
| [`signal_coordination`](#signal_coordination) |  | `signal_coordination.csv` | Establishes coordination for several signal controllers, associated with a timing_plan. |
| [`signal_phase_mvmt`](#signal_phase_mvmt) |  | `signal_phase_mvmt.csv` | Associates Movements and pedestrian Links (e.g., crosswalks) with signal phases.… |
| [`signal_timing_plan`](#signal_timing_plan) |  | `signal_timing_plan.csv` | For signalized nodes, establishes timing plans. |
| [`signal_timing_phase`](#signal_timing_phase) |  | `signal_timing_phase.csv` | For signalized nodes, provides signal timing and establishes phases that may run concurrently. |
| [`signal_detector`](#signal_detector) |  | `signal_detector.csv` | A signal detector is associated with a controller, a phase and a group of lanes. |
| [`segment_tod`](#segment_tod) |  | `segment_tod.csv` | An optional file that handles day-of-week and time-of-day restrictions on segments.… |
| [`lane_tod`](#lane_tod) |  | `lane_tod.csv` | An optional file that handles day-of-week and time-of-day restrictions on lanes that traverse entire links. |
| [`segment_lane_tod`](#segment_lane_tod) |  | `segment_lane_tod.csv` | An optional file that handles day-of-week and time-of-day restrictions on lanes within segments of links. |
| [`zone`](#zone) |  | `zone.csv` | Locates zones (travel analysis zones, parcels) on a map.… |
| [`config`](#config) |  | `config.csv` | Configuration information for the dataset (units, coordinate systems, etc.). |
| [`curb_seg`](#curb_seg) |  | `curb_seg.csv` | Provides a separate segment object for curbside regulations, which may change at different locations than segment-level changes to the travel lanes. |
