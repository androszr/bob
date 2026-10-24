# Bob mascot drawings

The twenty frog drawings the panel uses (`/static/bob/<name>.svg`). A role in `roles.json` picks one with its
`"obraz"` field; the page picks the others for empty states and the status hero. Every drawing is a self-contained
SVG with its own CSS animation (no scripts).

| File | What it shows | Used for |
|------|---------------|----------|
| `bob-static-base.svg` | Frog standing, no animation | Fallback, default picture |
| `bob-happy.svg` | Smiling frog bouncing | Success, "all is well" |
| `bob-sleeping.svg` | Frog lying down, ZZZ bubbles | Idle, nothing to do |
| `bob-wake.svg` | Frog waking up | Starting again |
| `bob-idle-living.svg` | Frog breathing quietly | Online, standing by |
| `bob-dizzy.svg` | Spiral eyes, swaying | Overload, confusion |
| `bob-disconnected.svg` | X eyes, broken signal | Offline, no connection |
| `bob-notification.svg` | Frog with an exclamation mark | Something needs attention |
| `bob-eureka.svg` | Light-bulb moment | Notes, discoveries |
| `bob-grooving.svg` | Dancing with music notes | Everything flows |
| `bob-going-away.svg` | Waving goodbye | Signing off |
| `bob-hat-mishap.svg` | Hat falling off | Light-hearted mishap |
| `bob-walking.svg` | Frog hopping forward | Loading, in transit |
| `bob-working-thinking.svg` | Thought bubble with dots | General assistant, thinking |
| `bob-working-confused.svg` | Scratching head, question mark | Unclear input |
| `bob-working-debugger.svg` | Magnifying glass over code | Project work, tickets, debugging |
| `bob-working-wizard.svg` | Wizard hat and wand | Impressive result |
| `bob-working-overheated.svg` | Exhausted, smoke rising | Rate limits, overload |
| `bob-working-juggling.svg` | Juggling coloured balls | Many things at once |
| `bob-fishing.svg` | Fishing, bobber dips | Waiting for a long job |
