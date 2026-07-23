Classify this email reply from a business owner who was sent an unsolicited
website-redesign mockup and pitch.

Reply text:
---
{reply_text}
---

Classify into exactly one of: interested, not_interested, needs_info,
unclear.

- interested: any positive signal about wanting the site, wanting to talk, or
  asking about next steps/price (even if hesitant).
- not_interested: any clear decline, "not interested", "remove me", "stop
  emailing", hostile tone.
- needs_info: asks a specific question (price, who are you, how does this
  work) without stating interest or disinterest yet.
- unclear: auto-reply, out-of-office, empty, or genuinely ambiguous.

Return ONLY valid JSON, no markdown:
{{"classification": "<interested|not_interested|needs_info|unclear>", "reason": "<one sentence>"}}
