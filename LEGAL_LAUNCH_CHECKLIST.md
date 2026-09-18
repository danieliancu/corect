# Legal launch checklist — Corect.uk

The application now contains Terms, a Privacy notice, versioned acceptance, opt-in analytics and a production check for
blank legal identity. **That does not make Corect.uk legally compliant by itself.** The items below are business or
professional actions that code cannot complete. None is done just because a configuration field exists.

## Identity and contact

- [ ] Decide the legal operator. Today there is no company: an individual can trade as Corect.uk
      (`LEGAL_OPERATOR_TYPE=sole_trader`). Set `LEGAL_OPERATOR_NAME` to that person's full legal name.
- [ ] Choose a service/correspondence address you can receive legal post at and set `LEGAL_SERVICE_ADDRESS`
      (consider a service address instead of a home address).
- [ ] Create a monitored public mailbox and set `CONTACT_EMAIL`.
- [ ] If a Ltd/CIC is incorporated later: set `LEGAL_OPERATOR_TYPE=company`, the company name, `COMPANY_NUMBER`,
      `VAT_NUMBER` if registered, review Terms/Privacy wording, and raise `TERMS_VERSION`/`PRIVACY_VERSION` so users
      accept the new operator.

## Data protection

- [ ] Assess whether the ICO data protection fee applies and register/pay if it does (ico.org.uk).
- [ ] Choose the production hosting and database provider; sign/accept its data processing agreement (DPA); set
      `LEGAL_HOSTING_PROVIDER`; confirm where data is stored and the server/access-log retention period.
- [ ] Review the OpenAI wording against the final production setup: API data processing addendum, contracting entity,
      data retention (including abuse monitoring), international transfer mechanism, and whether `store=False` wording
      is still accurate.
- [ ] Confirm with OpenAI (organisation settings, contract) the retention that applies to this account: standard API
      abuse-monitoring retention, or Zero Data Retention if approved. The Privacy notice deliberately says only that
      `store=False` is requested for text and exercise requests and that OpenAI may keep API data for a limited period;
      it must not be changed to promise zero retention unless OpenAI confirms it in writing. Moderation, transcription,
      realtime and speech calls have no `store` option.
- [ ] Decide retention periods still marked "nestabilit" in the Privacy notice: usage ledgers (`UsageEvent`,
      `AudioUsageEvent`), anonymous visitor records, server logs. Implement automatic deletion for them, then update
      `apps/core/legal.py:retention_facts` and raise `PRIVACY_VERSION`.
- [ ] Schedule `python manage.py cleanup_assistant` daily in production (the stated retention for sessions, rate-limit
      counters, duplicate-submission claims and live-session rows depends on it).
- [ ] Decide how data access and portability requests are fulfilled (who exports what, within one month).
- [ ] **Owner review of Privacy notice wording changed without raising `PRIVACY_VERSION`** (pre-launch hardening,
      September 2026): the email address is now required for new accounts and unique (section 3); the learning
      exercise requests also use `store=False` (section 6); first-party funnel events (section on analytics). Decide
      whether these changes are material enough to raise `PRIVACY_VERSION`, which asks every visitor to accept again.
- [ ] Decide whether and when to use account email addresses for service messages (password reset, account notices);
      no email is sent today and addresses are not verified.

## Notice, analytics and acceptance, verified in production

- [ ] **Legal review of the product decisions made here:** first-visit Terms/age acceptance is a notice bar with only
      "Am înțeles" (no tick box, nothing blocked), and anonymous analytics (`corect_visitor_id`) is **on by default**
      with an opt-out in "Setări cookie-uri". Under PECR, non-essential analytics cookies have generally required prior
      consent; the Data (Use and Access) Act 2025 adds exemptions for statistical cookies with clear information and an
      easy way to object. Confirm whether that exemption is in force and applies, or switch analytics back to opt-in
      (`apps/core/consent.py:analytics_chosen`).
- [ ] First visit: the notice bar appears on every page until "Am înțeles"; its Terms and Privacy links work.
- [ ] `corect_visitor_id` appears after the first „Vreau să sune natural!” or voice action unless analytics was
      switched off.
- [ ] Switching analytics off in "Setări cookie-uri" deletes `corect_visitor_id` and it does not come back.
- [ ] Signup requires the checkbox and records a `LegalAcceptance` row with both versions.
- [ ] A version bump shows the notice again to visitors and signed-in users. `TERMS_VERSION` and `PRIVACY_VERSION`
      were raised to `2026-09-15` when the two buttons (Corectare/Traducere) became one action and the usage ledger
      started recording the detected source language and processing durations; include that wording in the review.
- [ ] Account deletion from Profile removes the account and history.

## Abuse guardrails

- [ ] Keep `CONTENT_MODERATION_ENABLED=true` in production and watch `content_blocked`, `instruction_attempt` and
      `moderation_unavailable` counts in the staff analytics for false positives or outages (the check fails closed).
- [ ] Review real refusals with a native speaker during beta: learners' ordinary sentences must not be blocked.
- [ ] Decide who may suspend accounts in `/admin/` (Users → "Suspend selected accounts") and how appeals sent to the
      contact address are handled.
- [ ] Confirm the Privacy notice wording about OpenAI moderation matches the final provider terms.

## Learning profile and learning AI

- [ ] Run `python manage.py rebuild_learning_profiles` once after deploying, so existing learners see their patterns.
- [ ] Confirm the learning AI price: `OPENAI_LEARNING_MODEL` must have a row in `OPENAI_PRICING`, otherwise learning
      costs show "—" in the staff analytics. Review real cost per active learner after the first weeks.
- [ ] Review a sample of live `pattern` keys from prompt `2026-09-v8-naturalize` with a native speaker and tune the taxonomy.
- [ ] Confirm the Privacy notice section "Profilul tău de învățare" (profile kept until account deletion, minimal
      context sent to OpenAI) with the legal review below.
- [ ] Before selling Pro, decide which learning features are Pro-only and set `PRO_ENTITLEMENTS_ENFORCED=true`.

## Professional review

- [ ] Have the Terms and Privacy notice reviewed by a qualified UK legal professional before commercial launch
      (consumer law, UK GDPR/DPA 2018, PECR cookies, age threshold, governing law wording).
- [ ] Confirm the Romanian-language documents are acceptable as the governing texts, or add an English version.

## Before selling Pro

- [ ] Choose a payment provider and add its disclosures to the Privacy notice (recipient, data shared, transfers).
- [ ] Publish pricing, billing, renewal, cancellation and refund terms, including the UK 14-day cancellation right for
      digital services and how it is waived or applied.
- [ ] Review the daily plan limits with the legal review: 5 naturalisations a day without an account, 20 with a free
      account and, for Pro, "Cereri nelimitate (Fair Use)" with a technical ceiling of 200 a day stated in the Terms
      (section 13), reset at midnight UK time and not charged for failed requests. Check the "nelimitate" wording against
      the ASA/CAP guidance on "unlimited" claims while the 200 ceiling is enforced. `TERMS_VERSION` was raised to
      `2026-09-17` for this.
- [ ] Review the promotional price presentation (£9.99 struck through, £4.99/lună) against pricing rules: the normal
      price must be genuine.
