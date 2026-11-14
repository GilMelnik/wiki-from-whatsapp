import { Bidi, countText, formatCount, Ltr, stanceOf } from "@shell";
import { quoteParts } from "./suggestions.js";

// What the owner judges a suggestion by, from the wiki as it is now (GET /suggestions/{id} →
// evidence): the quoted sentence in its paragraph, the claim groups a sentence rests on (text,
// stance, supporters / opposers, dates, main page, the page sentences that cite it), the two
// sides of a contradiction, and the pages a suggestion touches with their scope and size.

function Said({ said }) {
  if (!said?.length) return <p className="text-xs text-slate-500">No sentence in the wiki cites this group.</p>;
  return (
    <ul className="space-y-1">
      {said.map((place) => (
        <li key={place.page_id} className="text-xs">
          <span className="text-slate-500">
            Cited on “<Bidi>{place.title}</Bidi>”:
          </span>
          {place.sentences.map((sentence, index) => (
            <Bidi as="p" key={index} className="mt-0.5 border-s-2 border-slate-200 ps-2 text-slate-700">
              {sentence}
            </Bidi>
          ))}
        </li>
      ))}
    </ul>
  );
}

export function GroupCard({ group }) {
  if (group.missing) {
    return (
      <li className="card text-xs text-slate-500">
        Group <Ltr>{group.group_id}</Ltr> is no longer in the groups file.
      </li>
    );
  }
  const stance = stanceOf(group.stance);
  return (
    <li className="card space-y-2">
      <Bidi as="p" className="leading-relaxed">
        {group.claim_text}
      </Bidi>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600">
        <span className={`badge ${stance.badge}`}>{stance.label}</span>
        <span>{countText(group.supporters, "supporter")}</span>
        <span className={group.opposers > 0 ? "font-medium text-rose-700" : ""}>{countText(group.opposers, "opposer")}</span>
        <span>
          <Ltr>{group.first_date}</Ltr>
          {group.last_date !== group.first_date && (
            <>
              {" – "}
              <Ltr>{group.last_date}</Ltr>
            </>
          )}
        </span>
        {group.primary_title && (
          <span>
            Main page: “<Bidi>{group.primary_title}</Bidi>”
          </span>
        )}
        <Ltr className="text-slate-400">{group.group_id}</Ltr>
      </p>
      <Said said={group.said_in} />
    </li>
  );
}

export function Groups({ groups }) {
  if (!groups.length) return <p className="text-slate-500">The suggestion names no claim groups.</p>;
  return (
    <ul className="space-y-2">
      {groups.map((group) => (
        <GroupCard key={group.group_id} group={group} />
      ))}
    </ul>
  );
}

export function PageCard({ page }) {
  if (!page.exists) {
    return (
      <li className="note note-danger">
        Page <Ltr>{page.page_id}</Ltr> is not in the plan now.
      </li>
    );
  }
  return (
    <li className="card space-y-1">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <Bidi className="font-medium">{page.title}</Bidi>
        {page.path.length > 0 && <Bidi className="text-xs text-slate-500">{page.path.join(" › ")}</Bidi>}
        <Ltr className="text-xs text-slate-400">{page.page_id}</Ltr>
      </p>
      <Bidi as="p" className="text-xs text-slate-600">
        {page.description}
      </Bidi>
      <p className="text-xs text-slate-500">
        Main page of {countText(page.groups.main, "group")}, mentions {formatCount(page.groups.mention)} more ·{" "}
        {page.written ? countText(page.words, "word") : "not written yet"}
      </p>
      <details className="text-xs">
        <summary className="cursor-pointer text-slate-500">Scope</summary>
        <Bidi as="p" className="mt-1 whitespace-pre-line leading-relaxed text-slate-700">
          {page.scope}
        </Bidi>
      </details>
    </li>
  );
}

export function Pages({ pages }) {
  return (
    <ul className="space-y-2">
      {pages.map((page) => (
        <PageCard key={page.page_id} page={page} />
      ))}
    </ul>
  );
}

/** The quoted sentence highlighted in its paragraph, or the sentence alone when it is gone. */
export function Quote({ quote, sentence }) {
  if (!quote) {
    return (
      <div className="note note-warning space-y-1">
        <p>
          The sentence is no longer on the page as it is now (the page changed since the review, or the suggestion was
          already applied). The sentence the review quoted:
        </p>
        <Bidi as="p" className="leading-relaxed">
          {sentence}
        </Bidi>
      </div>
    );
  }
  const [before, quoted, after] = quoteParts(quote);
  return (
    <Bidi as="p" text={quote.text} className="card leading-relaxed">
      {before}
      <mark className="rounded bg-amber-200 px-0.5 text-slate-900">{quoted}</mark>
      {after}
    </Bidi>
  );
}

export function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

export function Sides({ sides }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div>
        <p className="mb-1 text-xs font-medium text-slate-600">One side</p>
        <Groups groups={sides.a} />
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-slate-600">The other side</p>
        <Groups groups={sides.b} />
      </div>
    </div>
  );
}

/** For a plan change: the pages it touches, the category a new page joins, the labels of a kind. */
export function StructuralEvidence({ evidence }) {
  return (
    <>
      <Section title="Pages it touches">
        {evidence.pages.length ? <Pages pages={evidence.pages} /> : <p className="text-slate-500">It names no existing page.</p>}
      </Section>
      {evidence.parent && (
        <Section title={<>Pages already in “<Bidi>{evidence.parent.title}</Bidi>”</>}>
          {evidence.parent.pages.length ? <Pages pages={evidence.parent.pages} /> : <p className="text-slate-500">The category has no pages.</p>}
        </Section>
      )}
      {evidence.labels && (
        <Section title="Labels of the same kind">
          {evidence.labels.length ? (
            <ul className="space-y-1">
              {evidence.labels.map((label) => (
                <li key={label.label_id} className="card p-2 text-xs">
                  <Bidi className="font-medium">{label.title}</Bidi> <Ltr className="text-slate-400">{label.label_id}</Ltr>
                  <Bidi as="p" className="text-slate-600">
                    {label.description}
                  </Bidi>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">There is no label of this kind yet.</p>
          )}
        </Section>
      )}
    </>
  );
}
