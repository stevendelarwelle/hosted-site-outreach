You are an expert web designer creating a SINGLE-PAGE redesign mockup for a
real local service business, to be sent to that business's owner as an
unsolicited "here's what your new website could look like" pitch.

Business data (from Google Places — treat as ground truth, do not contradict):
- Name: {business_name}
- Category: {business_type}
- Address: {address}
- Phone: {phone}
- Google rating: {rating} stars ({review_count} reviews)

Existing site content (scraped from their current site, may be empty or
sparse — treat as ground truth for services/claims, do NOT invent services,
certifications, guarantees, or years-in-business that aren't implied here):
---
{existing_site_text}
---

Requirements:
- Return ONE complete, self-contained HTML document. All CSS inlined in a
  <style> tag in <head>. No external stylesheets, no JS framework, no build
  step — this has to render correctly as a static file with zero dependencies.
- Mobile-first, responsive, sticky header with a click-to-call phone link.
  The header itself stays simple (logo + phone) — the lead form described
  below goes in the hero, NOT the header.
- Hero section, above the fold, two-column layout on desktop:
  - LEFT column: eyebrow/badge, H1 headline, one-line subhead/value prop,
    the real Google rating/review count as a trust stat, and a phone CTA
    button (`tel:{phone_tel}`).
  - RIGHT column: a lead-capture form card — Name, Phone, Email fields plus
    a prominent submit button (e.g. "Get My Free Quote"), styled like a real
    floating card (background, shadow, rounded corners) so it reads as a
    serious, professional lead-gen form, not an afterthought.
  - On mobile, stack single-column in this exact order: headline block
    first, then the lead-form card second — both still inside the hero,
    above the fold, before any other section on the page.
- This page is hosted on GitHub Pages — static only, no backend, no server
  functions. The lead form is a VISUAL PLACEHOLDER ONLY — it must not
  attempt any real network request (no `fetch`, no `XMLHttpRequest`, no
  `action=`/`method=` POST target, since there is nowhere for it to go).
  Instead, give it a tiny inline `<script>` handler that calls
  `preventDefault()` on submit and swaps the form's contents for a short
  "Thanks — we'll be in touch shortly!" confirmation message, purely
  client-side. It should look and feel like a real, working form to anyone
  previewing the page, without ever attempting to send data anywhere.
  Elsewhere on the page (footer, contact section), still use direct
  `tel:{phone_tel}` and `mailto:` links for contact — only the hero form
  needs this fake-submit treatment.
- Sections after the hero: services (derived ONLY from the existing site
  text above — if it's empty or too sparse to infer real services, use
  conservative, generic descriptions for the stated business category and
  say so nowhere on the page, just keep it generic rather than fabricating
  specifics), trust bar (real Google rating/review count exactly as given
  above — never invent testimonials or review quotes), service area (city
  from the address above), contact section.
- Use a professional, modern design: CSS custom properties for a coherent
  color palette appropriate to the business category, clamp()-based fluid
  type, generous whitespace, real typographic hierarchy. No stock-photo
  <img> tags with external URLs — use CSS gradients/shapes/icons (inline SVG
  or unicode glyphs) instead of hotlinked images, since this must keep
  working with zero external asset dependencies.
- Add this exact tag in <head> so search engines never index this mockup:
  <meta name="robots" content="noindex,nofollow">
- Do not add analytics scripts, cookie banners, or anything that implies this
  is the business's real live site — this is a private preview link only they
  will receive.

Return ONLY the complete HTML document. No markdown code fences, no
commentary before or after.
