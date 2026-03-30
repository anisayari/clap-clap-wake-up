# Design System Strategy: The HUD Interface



## 1. Overview & Creative North Star

The Creative North Star for this system is **"The Kinetic Architect."**



This design system rejects the static, flat nature of traditional web dashboards in favor of a living, breathing Heads-Up Display (HUD). We are not building a website; we are building an experimental flight interface. The goal is to move beyond "Iron Man" tropes into a sophisticated, high-fidelity experience that feels airborne and data-dense.



Through **intentional asymmetry** and **calculated light emission**, we create a sense of depth that implies the UI is floating between the user and the data. We bypass standard grid rigidity by utilizing overlapping "holographic" panes and nested diagnostic elements that feel modular and urgent.



---



## 2. Colors: Luminance & Atmosphere

Our palette is rooted in a deep-space void (`#0a0e14`), punctuated by high-energy luminescence. Color here is not just decorative; it represents state and energy levels.



* **Primary (`#81ecff`)**: This is your "Ignition" color. Use it for critical data readouts and primary active states.

* **Secondary/Tertiary (`#ff7350`, `#c2ff99`)**: These are your "Warning" and "System Health" indicators. Use sparingly to maintain the "Electric Blue" dominance.

* **The "No-Line" Rule:** Standard 1px solid borders are strictly prohibited for structural sectioning. Instead, define boundaries through **Surface-Container shifts**. A `surface-container-low` panel sitting on a `background` provides all the separation a high-end UI needs.

* **The "Glass & Gradient" Rule:** To achieve the holographic feel, all panels must utilize a combination of `surface-variant` with a `backdrop-blur` (12px–20px) and a subtle linear gradient (from `primary` at 10% opacity to `transparent`).

* **Signature Textures:** Apply a 2px scanning-line pattern or a micro-noise texture to `surface-container-highest` elements to simulate a physical glass projection.



---



## 3. Typography: The Technical Editorial

Typography must feel like it was rendered by a tactical computer, not a word processor.



* **Display & Headline (Space Grotesk):** Chosen for its geometric, futuristic personality. Use `display-lg` (3.5rem) for high-impact data points (e.g., "CORE TEMP: 98%").

* **Body & Titles (Inter):** While the HUD is technical, readability remains paramount. `Inter` provides the clean, sans-serif balance needed for complex data strings.

* **The Label Role:** `label-sm` (`0.6875rem`) in all-caps with 0.1rem letter spacing is your workhorse for metadata, timestamps, and axis labels.

* **Visual Hierarchy:** Contrast a massive `display-sm` metric against a tiny, muted `label-sm` unit to create the "High-Tech Editorial" look found in premium cinema HUDs.



---



## 4. Elevation & Depth: Tonal Layering

We do not use drop shadows in this system. Shadows imply a light source from above; HUDs emit their own light.



* **The Layering Principle:** Depth is achieved by "stacking."

* **Base:** `surface` (#0a0e14)

* **Mid-Level:** `surface-container-low` (for secondary widgets)

* **Top-Level:** `surface-bright` (for active floating modals)

* **Ambient Glow:** Instead of shadows, use **Outer Glows**. When a component is active, use a blurred `primary_dim` shadow with a 0% offset and 20px blur at 15% opacity. This mimics light bleeding from a holographic projection.

* **The "Ghost Border" Fallback:** If a container requires a stroke, use `outline_variant` at 20% opacity. It should look like a faint light-leak at the edge of a glass pane, never a solid enclosure.

* **Angular Cutouts:** Use CSS `clip-path` to create chamfered (clipped) corners on containers, reinforcing the "machined" aesthetic without using standard border-radii.



---



## 5. Components: Machined Precision



### Buttons

* **Primary:** High-intensity `primary` background. No rounded corners (`0px`). Use a "glitch" hover effect or a rapid-pulse glow.

* **Tertiary:** Text-only with a flanking `px` (1px) vertical line on the left. This creates a "sidebar" navigation feel without a box.



### Data Visualizations (The Core)

* **Circular Gauges:** Use `primary` for the active track and `surface-variant` for the background track. Add a `primary_fixed` glow to the "needle" or head of the progress bar.

* **Holographic Cards:** Forbid divider lines. Use `surface-container-highest` for the header area and `surface-container` for the body. Separate content using the `spacing scale` (e.g., `8` (1.75rem)).



### Input Fields

* **States:** Default state is a bottom-only border using `outline_variant`. On focus, the border animates to full-width `primary` with a 2px "glow" under-lighting the text.



### HUD Metadata

* **Coordinate Chips:** Small, non-interactive chips showing "LAT/LONG" or "SYS_STATUS." These use `surface-variant` with `label-sm` text.



---



## 6. Do’s and Don’ts



### Do:

* **Use Asymmetry:** Place a large gauge on the left and a dense stack of small text labels on the right. It feels more intentional and "custom-built."

* **Embrace Transparency:** Allow the background (or background visualizations) to peek through UI panels using 80-90% opacity.

* **Animate Transitions:** Use "staggered" entry animations. Panels should slide and fade in one by one, mimicking a system boot sequence.



### Don't:

* **No Rounded Corners:** The `roundedness scale` is strictly `0px`. Roundness feels consumer-grade; sharp angles feel military-grade.

* **No Heavy Borders:** Never use a 100% opaque border. It "traps" the light and kills the holographic illusion.

* **No Generic Icons:** Avoid rounded, "bubbly" icons. Use thin-stroke, geometric, or monolinear icons that match the technical weight of `Space Grotesk`.
