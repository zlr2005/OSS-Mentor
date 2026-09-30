---
version: 1
slug: "web-recommendations-html"
primary_target: "web/recommendations.html"
related_targets: ["web/assets/recommendations.css","web/assets/recommendations.js"]
---

Scope: `/recommendations` logged-in recommendation review surface. Mode: Operate.

Audience and job: an authenticated contributor reviews ranked, currently available OSS tasks using the current owned developer profile, understands why each task fits, opens a task, or records that it is unsuitable.

Content and constraints: show profile/track/run context, 0-1 score rendered as a readable percentage, structured reasons with evidence and deltas, matched/missing skills, warnings and feedback state. Cover loading, empty, 401, missing-profile, service-error and feedback-error states. The HTTP route is owned by Member D.

Direction: a recommendation review desk extending the existing paper/forest/clay OSS-Mentor world. A dark context ledger anchors the left rail while a spacious ranked evidence list occupies the right. The memorable moment is reading each score as an evidence ledger rather than a decorative metric.

Unresolved: Member D must finalize `/api/v1/me/recommendations` and session-derived feedback submission semantics.
