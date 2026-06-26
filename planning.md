# planning.md — AI Attribution Detection API

---

## Architecture Narrative

A piece of text enters the system through an HTTP endpoint and travels through five
 components before a label reaches the user.

**1. Submission Endpoint (`POST /submit`)**
The creator or platform sends a JSON payload containing the text to be analyzed. This
component handles input validation (is the text present? is it within length limits?), applies
rate limiting (rejecting requests that exceed the configured threshold), and assigns the
submission a unique `content_id` that follows it through the rest of the pipeline. Nothing
about authorship is decided yet and this component only ensures the request is valid.

**2. LLM Signal**
The raw text is forwarded to a Groq-hosted language model with a structured prompt. The prompt
asks the model to assess whether the text reads as human-written or AI-generated, and to return
a score between 0.0 (confident human) and 1.0 (confident AI). The model examines semantic
coherence, stylistic consistency, phrasing naturalness, and tonal variation holistically. 
The output of this component is a single float: `llm_score`.

**3. Stylometric Signal**
While the LLM call is in flight (or immediately after), the raw text is also passed to a
Python heuristics engine. This component computes four measurable statistical properties:
sentence length variance, vocabulary diversity, punctuation density, and
average sentence complexity. Each property is normalized and combined into a composite
`stylometric_score` between 0.0 and 1.0, where higher values indicate AI uniformity. No
external API call is needed as this runs entirely in-process.

**4. Confidence Scoring Engine**
The two scores (`llm_score` and `stylometric_score`) are combined here into a single
`confidence_score` and an `attribution` label (`human`, `ai`, or `uncertain`). The engine uses
a weighted average (60% LLM, 40% stylometric) to produce a raw score, then maps it to
attribution and a transparency label. Critically, when the two signals disagree significantly
(e.g., one says 0.8 AI, the other says 0.3 AI), the engine reduces confidence and routes the
result toward `uncertain` rather than forcing a binary decision. The disagreement itself is
evidence of uncertainty.

**5. Audit Logger**
Before the response is returned, every attribution decision including the `content_id`, timestamp, raw
text hash, both signal scores, combined confidence, attribution result, and label text is
written to a structured audit log (append-only JSON lines). This happens synchronously so no
decision can be returned without being recorded.

**6. Response**
The structured JSON response is returned to the caller. It contains: `content_id`,
`attribution` (`human` / `ai` / `uncertain`), `confidence_score`, `label_text` (the exact
string shown to the reader), both raw signal scores, and a `status` field (`complete`).

The appeal flow diverges from this path. When a creator disputes a result, a `POST /appeal`
request carries the `content_id` and the creator's written reasoning. The appeal component
logs the dispute alongside the original decision, updates the content's `status` to
`under_review`, and returns a confirmation. No re-classification occurs automatically — a human
reviewer consults the audit log.

---

## Detection Signals

### Signal 1 — LLM-Based Classification (Groq)

**What property it measures:**
Semantic and stylistic coherence as perceived by a language model. The classifier reads the
text holistically and assesses whether the overall voice, idea progression, phrasing choices,
and tonal consistency match patterns it associates with a human or AI.

**Why this property differs between human and AI writing:**
AI generated text tends to be locally coherent but globally smooth in a way that human writing
rarely is. Humans have unexpected word choices and structural irregularities that are hard to predict. AI writing optimizes
for readability in a way that an LLM classifier can
detect even when individual sentences look fine. The LLM signal captures the things that don't reduce to any single measurable feature.

**Blind spots:**
- High level professional writing may read as
  AI to the classifier.
- AI text that has been lightly edited by a human may read as human.
- The signal is a black box — when it's wrong, we can't see why.
- Prompt sensitivity: small changes to the classifier prompt can shift scores meaningfully.
- Short texts give the model too little to work with to be completely reliable.

---

### Signal 2 — Stylometric Heuristics (Python)

**What property it measures:**
Four measurable statistical properties of text surface structure:
- **Sentence length variance:** the standard deviation of word counts across sentences.
- **Word choice:** unique words divided by total words gives a measure of vocabulary
  diversity.
- **Punctuation density:** punctuation characters as a fraction of total characters.
- **Average sentence complexity:** average words per sentence estimates syntactic depth.

**Why this property differs between human and AI writing:**
AI text generation optimizes its speech and produces statistical
regularity as a side effect. Sentence lengths may also average out around a middle range.
Vocabulary repeats at predictable intervals. Punctuation is used conventionally and sparingly.
Human writing is generally messier and does not use punctuation expressively. The variance and diversity in human text
can be structurally detectable even before reading for meaning.

**Blind spots:**
- Writers who who naturally write in a regular, clear style will score
  as AI-like.
- Technical documentation is stylometrically similar to AI output meaning the origin of the input could affect scores
- Short texts produce unreliable variance.
- AI could be prompted in such a way to get past these filters.

---

## Anticipated Edge Cases

**Case 1 — Minimalist poetry with simple, repetitive vocabulary**
A poem written in a style with short lines, common words, or deliberate
repetition will score poorly on word and sentence length variance which are
the same properties that flag AI text. Likely outcome: an `uncertain` result.

**Case 2 — AI-generated text that has been substantially human-edited**
A user generates a blog post draft with an AI tool, then rewrites half the sentences, adds
personal stories, and changes the structure. The LLM signal may still detect AI
phrasing patterns in the unedited portions and returns `uncertain`. 

**Case 3 — Technical or instructional writing by a human**
A human writing a how-to guide or API documentation naturally produces text that is
 similar to AI output with consistent sentence length and precise vocabulary. Also likely to return `uncertain`.


---

## API Surface

### `POST /submit`

**Purpose:** Submit text for attribution analysis.

**Request body:**
```json
{
  "content_id": "string (optional — generated if omitted)",
  "text": "string (required)",
  "content_type": "string (optional — e.g. 'poem', 'blog_post', 'short_story')"
}
```

**Response:**
```json
{
  "content_id": "string",
  "attribution": "human | ai | uncertain",
  "confidence_score": 0.0,
  "label_text": "string (exact text shown to reader)",
  "signals": {
    "llm_score": 0.0,
    "stylometric_score": 0.0
  },
  "status": "complete",
  "timestamp": "ISO 8601 string"
}
```

**Rate limit:** 10 requests per minute per IP. Returns HTTP 429 on excess.

---

### `POST /appeal`

**Purpose:** Allow a creator to contest a classification.

**Request body:**
```json
{
  "content_id": "string (required)",
  "creator_reasoning": "string (required — creator's explanation)"
}
```

**Response:**
```json
{
  "content_id": "string",
  "appeal_id": "string",
  "status": "under_review",
  "message": "Your appeal has been received and logged. A reviewer will examine your submission.",
  "timestamp": "ISO 8601 string"
}
```

---

### `GET /result/{content_id}`

**Purpose:** Retrieve the current attribution result and status for a previously submitted piece.

**Response:**
```json
{
  "content_id": "string",
  "attribution": "human | ai | uncertain",
  "confidence_score": 0.0,
  "label_text": "string",
  "status": "complete | under_review | reviewed",
  "timestamp": "ISO 8601 string"
}
```

---

### `GET /log`

**Purpose:** Retrieve the audit log (admin/reviewer use). Returns the most recent N entries.

**Query parameters:** `?limit=50&offset=0`

**Response:**
```json
{
  "entries": [
    {
      "entry_id": "string",
      "content_id": "string",
      "event_type": "attribution | appeal",
      "attribution": "human | ai | uncertain",
      "confidence_score": 0.0,
      "signals": {
        "llm_score": 0.0,
        "stylometric_score": 0.0
      },
      "label_text": "string",
      "status": "complete | under_review",
      "appeal_reasoning": "string (present only for appeal events)",
      "timestamp": "ISO 8601 string"
    }
  ],
  "total": 0
}
```

---

## Architecture

### Submission Flow

```
Caller
  |
  | POST /submit  { text, content_id? }
  v
+------------------+
| Submission       |  — validates input
| Endpoint         |  — checks rate limit (10 req/min/IP)
|                  |  — assigns content_id
+------------------+
  |                  |
  | raw text         | raw text
  v                  v
+------------+  +-------------------+
| LLM Signal |  | Stylometric       |
| (Groq API) |  | Heuristics        |
|            |  | (Python)     |
| semantic & |  | sentence variance |
| stylistic  |  | TTR, punctuation  |
| coherence  |  | density, avg sent |
|            |  | complexity        |
+------------+  +-------------------+
  |                  |
  | llm_score (0–1)  | stylometric_score (0–1)
  v                  v
+----------------------------------+
| Confidence Scoring Engine        |
|                                  |
| weighted avg (60% LLM, 40% stylo)|
| signal disagreement → uncertain  |
| thresholds:                      |
|   ≥ 0.80 → high-confidence AI   |
|   ≤ 0.20 → high-confidence human|
|   else   → uncertain             |
+----------------------------------+
  |
  | attribution, confidence_score, label_text
  v
+------------------+
| Audit Logger     |  — appends structured entry to log
|                  |  — stores: content_id, timestamp,
|                  |    text hash, both signal scores,
|                  |    combined score, attribution,
|                  |    label_text
+------------------+
  |
  | structured response
  v
Caller  ←  { content_id, attribution, confidence_score,
             label_text, signals, status, timestamp }
```

### Appeal Flow

```
Creator
  |
  | POST /appeal  { content_id, creator_reasoning }
  v
+------------------+
| Appeal Endpoint  |  — validates content_id exists
|                  |  — assigns appeal_id
+------------------+
  |
  | appeal_id, content_id, reasoning
  v
+------------------+
| Audit Logger     |  — appends appeal entry to log
|                  |    (linked to original decision
|                  |     by content_id)
|                  |  — updates content status →
|                  |    "under_review"
+------------------+
  |
  | confirmation
  v
Creator  ←  { content_id, appeal_id, status: "under_review",
              message, timestamp }
```

---

## Transparency Label Variants

The exact text of each label, as it would appear to a reader:

**High-confidence AI (confidence_score ≥ 0.80):**
> "Our tools indicate this content was likely AI-generated. Two independent signals: one
> semantic and one structural. Both suggest AI authorship with high confidence. If you created
> this content yourself, you can submit an appeal and a reviewer will examine your submission."

**High-confidence Human (confidence_score ≤ 0.20):**
> "Our tools indicate this content was likely written by a human. Two independent signals:
> one semantic and one structural. Both suggest a human writer with high confidence."

**Uncertain (confidence_score between 0.21 and 0.79):**
> "We're not sure. Our tools found some patterns that sometimes appear in AI-generated writing,
> but not enough to reach a confident conclusion. This content has been marked as uncertain. If
> you created this content yourself, you can submit an appeal and a reviewer will examine your
> submission."

---

## Rate Limiting — Rationale

**Chosen limit:** 10 requests per minute per IP address.

**Reasoning:**
- Each submission triggers a Groq API call (external, metered) plus local computation. Costs
  scale directly with request volume, so a per-IP limit protects operating costs.
- Legitimate users — a writer checking their own work, a platform batch-processing submissions
  — rarely need more than 10 attributions per minute in an interactive context.
- 10 req/min is strict enough to deter bulk scraping or classifier probing (attempts to reverse-
  engineer the detection threshold by submitting many variations) while being permissive enough
  for normal use.
- Platform integrations that need higher throughput can be issued API keys with elevated limits
  as a separate tier.

---
