# 34 — AI Article Lecture Generator

LLM-drafted bodies for **article** lectures. The instructor opens a lesson's
content step, clicks *Generate article*, and the rich-text editor fills with a
draft they can read, edit, and then save like any other article.

Second service in the `Career-College-AI-Services` project, after the course
outline generator (`32-ai-course-outline-generator.md`). Same topology, same
trust model, same never-persist rule — read that doc first; this one covers only
what differs.

Repos: `Career-College-AI-Services` (`app/article_lecture_generator/`),
`Career-College-Backend` (`courses`), `Career-College-Frontend`
(`lesson-modal.tsx`). **No migration** — `Lecture.article_content` already
exists and already holds editor HTML.

---

## 1. Topology

```
Browser ──JWT──▶ Django  ──X-Service-Key──▶ AI services ──▶ Groq
         (course builder)  POST /v1/article-lecture/
```

One-directional, exactly as for outlines: the frontend never calls the AI
service, the AI service never touches this database, and `GROQ_API_KEY` never
exists in this repo. `AI_SERVICES_BASE_URL` / `AI_SERVICES_KEY` are unchanged —
a new AI feature adds a URL path, not an env var.

| Layer | File |
|---|---|
| Endpoint | `courses/all_views/ai_views.py` → `ArticleLecturePreviewAPIView` |
| Body validation | `courses/all_serializers/ai_serializers.py` → `ArticleLectureRequestSerializer` |
| HTTP client | `courses/services/ai_article_service.py` → `generate_article_lecture`, `AIArticleError` |
| Generation | `app/article_lecture_generator/{schemas,prompts,generator,router}.py` |
| Frontend client | `src/lib/course-api/ai.ts` → `generateArticleLecture` |
| Frontend UI | `.../course-builder/lesson-modal.tsx` — the "Write with AI" panel above the editor |

---

## 2. The endpoint

`POST /api/v1/courses/ai/article-lecture-preview/` —
`[IsAuthenticated, IsEmailVerified, IsCourseCreator]`, throttled by
`AIArticleThrottle`.

Request — `lecture_title` is the only required field:

| Field | Notes |
|---|---|
| `lecture_title` | The lesson's own title. The article is written from it |
| `course_title`, `section_title` | Context; makes the article specific to its place in the course |
| `description` | What the lesson should cover, in the instructor's words |
| `key_points` | ≤ 12 strings the article must cover — typically the outline generator's `description` for this item |
| `audience`, `level`, `language` | Already declared on the course |
| `target_duration_minutes` | Target **reading** time, 0–120. Becomes a word budget upstream |
| `include_code_examples` | Default `false` |
| `extra_instructions` | Free-text steer; also what makes a regenerate differ |

Response `data`: `summary`, `sections[]`, `takeaways_heading`,
`key_takeaways[]`, `article_html`, `word_count`,
`estimated_reading_minutes`.

**It persists nothing.** The draft reaches the lecture only when the instructor
saves the modal, through the endpoint that already exists —
`PATCH /api/v1/courses/lectures/<id>/` with `lecture_type='article'` and
`article_content`. Never make this endpoint write: an AI body saved without a
human reading it satisfies `chk_lecture_payload_by_type` and passes
`_validate_course_completeness`, so it would sail into a published course with
nothing having checked it. The completeness rules are what stop hollow content
reaching learners; this endpoint must not route around them.

**Video lectures are out of scope by construction** — they need a real uploaded
file that must transcode to `ready`. Nothing here can produce one.

---

## 3. Why the HTML is rendered server-side

The model is asked for **structure only** — `summary`, `sections` (heading,
paragraphs, bullets, optional code), `key_takeaways`. `article_html` is built in
Python by `render_article_html()`. Two consequences, both deliberate:

1. **The preview and the saved value cannot diverge.** Same reasoning as
   `outline_text` on the outline service.
2. **The model cannot emit markup the editor drops.** The frontend editor is
   TipTap with `StarterKit`; anything outside it is discarded silently on load,
   which loses content with no error anywhere. The renderer emits only `<h2>`,
   `<p>`, `<ul>/<li>`, `<pre><code>`, `<em>`, and shapes list items as
   `<li><p>…</p></li>` — what TipTap itself serializes — so an article the
   instructor edits and saves again round-trips unchanged. `<h1>` is never
   emitted: the player already renders the lecture title above the body.

**Every string from the model is HTML-escaped before it reaches the markup**
(`_escape()` in the generator). The only angle brackets in `article_html` are
the ones this project writes. That matters because `article_content` is rendered
with `dangerouslySetInnerHTML` in the learner player and the admin review
preview — a model reply containing a tag must arrive as visible characters.

`takeaways_heading` is asked of the model rather than hardcoded, so a Bangla
article does not end with an English "Key takeaways".

---

## 4. Length, and why it is a word budget

`target_duration_minutes` is turned into a word count upstream
(`target_word_count()`, 180 wpm), clamped to 250–1600 words. The clamp is the
real spend control: the field is free-typed, and 1600 words is comfortably
inside `LLM_MAX_OUTPUT_TOKENS`, which is itself bounded by the Groq account's
tokens-per-minute limit. `MAX_SECTIONS` / `MAX_PARAGRAPHS` are sized to the same
budget — raising them without raising the tier turns long articles into
truncated JSON, one retry, then a 502.

`word_count` and `estimated_reading_minutes` are **computed from the prose**,
never claimed by the model. Code samples are excluded from the count — splitting
source on whitespace measures nothing a reader recognises as a word.

---

## 5. Code samples are opt-in

`include_code_examples` defaults to `false`. When it is off the prompt forbids
a `code` object **and** the generator strips any that arrive: the prompt is a
request, `_drop_code()` is the guarantee. A code block in a history lesson is
worse than no code block, and the model volunteers them freely.

---

## 6. Throttling and failure

`AIArticleThrottle` (`scope='ai_article'`, `AI_ARTICLE_RATE_LIMIT`, default
`10/min`) is the second throttle in the project that guards spend rather than
data integrity. **Its own scope, not shared with `ai_outline`** — outlining runs
once per course, drafting once per lesson, so one counter would let a single
outline session exhaust a writing session's budget.

Every upstream failure collapses to one **503** with one generic message
(`AIArticleError`) — network error, key mismatch, provider 502, unparseable body
are indistinguishable to the client. The reason is logged; it is never
forwarded. `REQUEST_TIMEOUT = (5, 45)`: the read leg stays above the AI service's
own 40 s LLM timeout so that service fails first with a real status.

Upstream, `/v1/article-lecture/` maps failures exactly like the outline service
— provider unreachable → 503, provider error → 502, unusable output after the
retry budget → 502.

---

## 7. Frontend

The panel lives in `lesson-modal.tsx`, directly above the rich-text editor, and
renders only for article lectures (step 2 of two-step authoring, and when
editing an existing article). It offers the focus steer, a reading-time input,
and the code-examples checkbox.

Two details worth keeping:

- **Generating replaces the editor's content, so the first click on a non-empty
  editor only arms the button.** The second click generates. No dialog.
- **`RichTextEditor` reads `value` only when it mounts** (`useEditor({content:
  value})` with no sync effect), so a new draft is invisible without a fresh
  instance. The modal bumps `editorVersion` and passes it as `key` to remount.
  That loses the editor's undo stack — acceptable precisely because a full
  replacement is what just happened, and it avoids touching a shared component
  used by the profile tabs, setup tab and admin modals.

Course/module context comes from `CurriculumTab`'s `meta` prop — the same live
Setup-form values the outline generator uses — so an unsaved Setup edit is
honoured here too.

---

## 8. Deliberately not built

- **No auto-apply.** The draft never writes itself onto the lecture (§2).
- **No provenance flag.** Nothing records that a lecture's body came from the
  AI. Unlike the outline apply, there is no reuse decision to make on a second
  run — the instructor is looking at the text while they replace it.
- **No caching and no streaming.** Each call regenerates from scratch, which is
  what makes *Regenerate* work; the throttle is the spend control.
- **No AI quiz questions, rubrics or evaluation scripts.** Each of those must be
  *complete* to be safe, and each is its own feature.
- **No video lectures** (§2).
